"""Direct headless call to /manage-reservation/viewres/api/reservation using saved cookies."""

import argparse
import json
import sys
from pathlib import Path

from aa_mcp_server.api import _IMPERSONATE, _USER_AGENT, BASE
from aa_mcp_server.auth import AASession
from curl_cffi import requests as curl_requests
from yarl import URL


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="default")
    ap.add_argument("--locator", required=True)
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", required=True)
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    sess = AASession(args.account)
    cookies = sess.cookies_dict()
    xsrf = cookies.get("XSRF-TOKEN", "")

    url = URL(BASE) / "manage-reservation/viewres/api/reservation"
    referer = (URL(BASE) / "reservation/selectReservationSubmit.do").with_query(
        {"recordLocator": args.locator}
    )

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": BASE,
        "Referer": str(referer),
        "User-Agent": _USER_AGENT,
        "X-XSRF-TOKEN": xsrf,
        "sec-ch-ua": '"Not/A)Brand";v="99", "Chromium";v="148"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    body = {
        "firstName": args.first,
        "lastName": args.last,
        "recordLocator": args.locator,
        "fromSource": True,
        "locale": "en_US",
    }
    resp = curl_requests.post(
        str(url),
        json=body,
        headers=headers,
        cookies=cookies,
        impersonate=_IMPERSONATE,
        timeout=30,
    )
    print(f"# status: {resp.status_code}", file=sys.stderr)
    print(f"# bytes: {len(resp.content)}", file=sys.stderr)

    out = json.dumps(resp.json(), indent=2, default=str)
    if args.out == "-":
        sys.stdout.write(out)
    else:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"# wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
