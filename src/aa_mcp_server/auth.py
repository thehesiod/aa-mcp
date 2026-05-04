"""AA MCP session management — cookie jar persistence + CDP extraction. Multi-account."""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import jwt
import websockets
from curl_cffi import requests as curl_requests
from yarl import URL

logger = logging.getLogger(__name__)

BASE_DIR = Path.home() / ".aa-mcp"
ACCOUNTS_DIR = BASE_DIR / "accounts"
CONFIG_FILE = BASE_DIR / "config.json"

AA_COOKIE_DOMAINS = (".aa.com", "www.aa.com", "aa.com", ".login.aa.com", "login.aa.com")


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(config: dict) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def _account_session_file(account: str) -> Path:
    return ACCOUNTS_DIR / account / "session.json"


def _load_account_session(account: str) -> dict | None:
    path = _account_session_file(account)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_account_session(account: str, data: dict) -> None:
    path = _account_session_file(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.DecodeError:
        return None


def list_accounts() -> list[str]:
    return _load_config().get("accounts", [])


def get_default_account() -> str | None:
    return _load_config().get("default")


async def _cdp_get_all_cookies(port: int) -> list[dict]:
    """Connect to a Chromium remote-debugging port and pull every cookie via CDP.

    Network.getAllCookies must be issued on a page-level WS, not the browser-level WS —
    the browser WS only exposes Browser/Target domains.
    """
    list_url = (URL("http://127.0.0.1") / "json").with_port(port)
    resp = curl_requests.get(str(list_url), timeout=10)
    resp.raise_for_status()
    pages = resp.json()

    page_ws: str | None = None
    for p in pages:
        if p.get("type") != "page":
            continue
        if "aa.com" in p.get("url", ""):
            page_ws = p["webSocketDebuggerUrl"]
            break
        if page_ws is None:  # fallback to first non-aa page
            page_ws = p["webSocketDebuggerUrl"]

    if page_ws is None:
        raise RuntimeError(
            f"No page targets on Chromium debug port {port}. "
            f"Open an aa.com tab in the browser first."
        )

    async with websockets.connect(page_ws, max_size=20 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                return msg.get("result", {}).get("cookies", [])


def extract_session_from_browser(account: str, port: int = 9224) -> dict:
    """Pull cookies from a logged-in Chromium (started by aa-auth-browser) and save them.

    Returns a status dict (account, num_cookies, aadvantage_number, token_exp).
    """
    cookies = asyncio.run(_cdp_get_all_cookies(port))
    aa_cookies = [
        c for c in cookies
        if any(c.get("domain", "").endswith(d.lstrip(".")) for d in AA_COOKIE_DOMAINS)
    ]
    if not aa_cookies:
        raise RuntimeError(
            f"No aa.com cookies found on Chromium debug port {port}. "
            f"Make sure you logged into aa.com in the browser launched by aa-auth-browser."
        )

    by_name = {c["name"]: c for c in aa_cookies}
    if "access_token" not in by_name:
        raise RuntimeError(
            "No access_token cookie found — log into aa.com (not just visit the homepage) "
            "before calling save_session_from_browser."
        )

    payload: dict[str, Any] = {
        "saved_at": int(time.time()),
        "cookies": aa_cookies,
    }

    info = _decode_access_token(by_name["access_token"]["value"])
    if info:
        payload["aadvantage_number"] = info.get("sub")
        payload["token_exp"] = info.get("exp")

    _save_account_session(account, payload)

    config = _load_config()
    accounts = config.get("accounts", [])
    if account not in accounts:
        accounts.append(account)
        config["accounts"] = accounts
    if not config.get("default"):
        config["default"] = account
    _save_config(config)

    logger.info("Saved %d cookies for account '%s'", len(aa_cookies), account)
    return {
        "account": account,
        "num_cookies": len(aa_cookies),
        "aadvantage_number": payload.get("aadvantage_number"),
        "token_exp": payload.get("token_exp"),
    }


class AASession:
    """Lazy-loaded cookie jar for a specific AAdvantage account."""

    def __init__(self, account: str | None = None) -> None:
        if account is None:
            account = get_default_account() or "default"
        self._account = account
        self._session: dict | None = None
        self._load()

    @property
    def account(self) -> str:
        return self._account

    def _load(self) -> None:
        self._session = _load_account_session(self._account)
        if self._session:
            logger.info("Loaded session for account '%s'", self._account)

    def reload(self) -> None:
        """Re-read from disk — call after a fresh extract_session_from_browser."""
        self._load()

    @property
    def is_authenticated(self) -> bool:
        return self._session is not None and bool(self._session.get("cookies"))

    def cookies_dict(self) -> dict[str, str]:
        if not self._session:
            raise RuntimeError(
                f"Account '{self._account}' has no saved session. "
                f"Run aa-auth-browser, log in, then call save_session_from_browser."
            )
        return {c["name"]: c["value"] for c in self._session.get("cookies", [])}

    def cookie_value(self, name: str) -> str | None:
        if not self._session:
            return None
        for c in self._session.get("cookies", []):
            if c["name"] == name:
                return c["value"]
        return None

    def aadvantage_number(self) -> str | None:
        if not self._session:
            return None
        return self._session.get("aadvantage_number")

    def is_token_expired(self, buffer_seconds: int = 60) -> bool:
        if token := self.cookie_value("access_token"):
            if info := _decode_access_token(token):
                exp = info.get("exp", 0)
                return time.time() >= (exp - buffer_seconds)
        return True

    def check_status(self) -> dict:
        info: dict = {
            "account": self._account,
            "session_file": str(_account_session_file(self._account)),
            "has_session": self.is_authenticated,
        }
        if self._session:
            info["saved_at"] = self._session.get("saved_at")
            info["num_cookies"] = len(self._session.get("cookies", []))
            info["aadvantage_number"] = self._session.get("aadvantage_number")
            info["token_exp"] = self._session.get("token_exp")
            info["token_expired"] = self.is_token_expired(buffer_seconds=0)
        return info
