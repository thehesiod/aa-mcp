"""One-off: attach to the existing Chromium on port 9224, navigate to a manage-trip
URL, and dump every /api/, /loyalty/, /services/ JSON request the SPA fires.

Run after: aa-auth-browser default (so the browser is up and you're logged in).
"""

import argparse
import asyncio
import json
import sys
from collections import OrderedDict
from typing import Any

import websockets
from curl_cffi import requests as curl_requests
from yarl import URL

INTEREST = (
    "/loyalty/api/",
    "/api/loyalty/",
    "/api/",
    "/services/graphql",
    "/manage-reservation/",
    "/reshop/",
    "/booking/",
)
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ico", ".gif")

AA_BASE = URL("https://www.aa.com")


async def find_or_create_aa_target(port: int, target_url: str) -> str:
    """Return a webSocketDebuggerUrl pointing at an aa.com page target.

    Prefers an existing aa.com tab; otherwise creates a new one navigated to target_url.
    """
    list_url = (URL("http://127.0.0.1") / "json").with_port(port)
    pages = curl_requests.get(str(list_url), timeout=10).json()
    for p in pages:
        if p.get("type") == "page" and "aa.com" in p.get("url", ""):
            return p["webSocketDebuggerUrl"]

    version_url = (URL("http://127.0.0.1") / "json" / "version").with_port(port)
    browser_ws = curl_requests.get(str(version_url), timeout=10).json()["webSocketDebuggerUrl"]
    async with websockets.connect(browser_ws, max_size=20 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Target.createTarget",
            "params": {"url": target_url},
        }))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                target_id = msg["result"]["targetId"]
                break
    await asyncio.sleep(1.0)
    pages = curl_requests.get(str(list_url), timeout=10).json()
    for p in pages:
        if p.get("targetId") == target_id or p.get("id") == target_id:
            return p["webSocketDebuggerUrl"]
    raise RuntimeError("Created target but couldn't find its WS URL")


def is_interesting(url: str) -> bool:
    if not any(s in url for s in INTEREST):
        return False
    if any(url.split("?", 1)[0].endswith(ext) for ext in SKIP_EXT):
        return False
    return True


async def capture(
    page_ws: str,
    target_url: str | None,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    """Capture interesting JSON requests for duration_seconds.

    If target_url is given, navigate there first; otherwise listen passively while
    the user drives the page manually.
    """
    requests_by_id: dict[str, dict[str, Any]] = {}
    next_id = iter(range(100, 1_000_000))

    async with websockets.connect(page_ws, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": next(next_id), "method": "Network.enable"}))
        await ws.send(json.dumps({"id": next(next_id), "method": "Page.enable"}))
        if target_url:
            await ws.send(json.dumps({
                "id": next(next_id),
                "method": "Page.navigate",
                "params": {"url": target_url},
            }))

        deadline = asyncio.get_event_loop().time() + duration_seconds
        get_body_pending: dict[int, str] = {}
        while True:
            timeout = max(0.0, deadline - asyncio.get_event_loop().time())
            if timeout == 0.0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)

            if "method" in msg:
                m = msg["method"]
                p = msg.get("params", {})
                if m == "Network.requestWillBeSent":
                    rid = p["requestId"]
                    req = p["request"]
                    if is_interesting(req["url"]):
                        requests_by_id[rid] = {
                            "url": req["url"],
                            "method": req["method"],
                            "headers": req.get("headers", {}),
                            "postData": req.get("postData"),
                            "type": p.get("type"),
                            "initiator": p.get("initiator", {}).get("type"),
                        }
                elif m == "Network.responseReceived":
                    rid = p["requestId"]
                    if rid in requests_by_id:
                        resp = p["response"]
                        requests_by_id[rid]["status"] = resp.get("status")
                        requests_by_id[rid]["mimeType"] = resp.get("mimeType")
                elif m == "Network.loadingFinished":
                    rid = p["requestId"]
                    if rid in requests_by_id and "body" not in requests_by_id[rid]:
                        body_id = next(next_id)
                        get_body_pending[body_id] = rid
                        await ws.send(json.dumps({
                            "id": body_id,
                            "method": "Network.getResponseBody",
                            "params": {"requestId": rid},
                        }))
            elif "id" in msg and msg["id"] in get_body_pending:
                rid = get_body_pending.pop(msg["id"])
                if rid in requests_by_id:
                    body = msg.get("result", {}).get("body")
                    if body and len(body) < 200_000:
                        requests_by_id[rid]["body_excerpt"] = body[:50_000]

        return list(requests_by_id.values())


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9224)
    ap.add_argument("--locator", help="Record locator, e.g. UHJHHT (used if --url not given)")
    ap.add_argument("--url", help="Full URL to navigate to (overrides --locator)")
    ap.add_argument(
        "--listen",
        action="store_true",
        help="Don't navigate; attach to the existing aa.com tab and listen passively while you drive the page.",
    )
    ap.add_argument("--duration", type=float, default=12.0)
    args = ap.parse_args()

    if args.listen:
        target_url = None
    elif args.url:
        target_url = args.url
    elif args.locator:
        target_url = str(
            (AA_BASE / "reservation/selectReservationSubmit.do").with_query(
                {"recordLocator": args.locator}
            )
        )
    else:
        ap.error("must pass --url, --locator, or --listen")
    page_ws = await find_or_create_aa_target(args.port, target_url or str(AA_BASE))
    print("# attached", file=sys.stderr)
    if target_url:
        print("# navigating: " + target_url, file=sys.stderr)
    else:
        print("# listening passively for {:.0f}s".format(args.duration), file=sys.stderr)

    items = await capture(page_ws, target_url, args.duration)

    summary = []
    for it in items:
        summary.append(OrderedDict([
            ("method", it.get("method")),
            ("status", it.get("status")),
            ("url", it["url"][:160]),
            ("hasBody", "body_excerpt" in it),
        ]))
    print(json.dumps({"summary": summary, "full": items}, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
