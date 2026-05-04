"""American Airlines AAdvantage MCP Server — read-only, multi-account."""

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP

from aa_mcp_server.api import AAAPI, default_activity_window
from aa_mcp_server.auth import (
    AASession,
    extract_session_from_browser,
    get_default_account,
    list_accounts,
)
from aa_mcp_server.auth_browser import DEFAULT_PORT

# stderr to avoid corrupting MCP stdio transport
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP("AA AAdvantage MCP Server")

_apis: dict[str, AAAPI] = {}


def _get_api(account: str = "") -> AAAPI:
    key = account or get_default_account() or "default"
    if key not in _apis:
        _apis[key] = AAAPI(AASession(key if account else None))
    return _apis[key]


def _evict(account: str) -> None:
    _apis.pop(account, None)


@mcp.tool()
def check_auth_status(account: str = "") -> str:
    """Check the saved AA session for an account — token expiry, AAdvantage #, cookie count.

    Args:
        account: Account name (optional, uses default).
    """
    api = _get_api(account)
    info = api._session.check_status()
    info["all_accounts"] = list_accounts()
    info["default_account"] = get_default_account()
    return json.dumps(info, indent=2, default=str)


@mcp.tool()
def save_session_from_browser(account: str = "default", port: int = DEFAULT_PORT) -> str:
    """Pull cookies from a logged-in Chromium (started by aa-auth-browser) and save them.

    Workflow:
      1. Run `aa-auth-browser <account>` to launch Chromium with --remote-debugging-port.
      2. Log into aa.com in that browser.
      3. Call this tool — it connects to the debug port via CDP and saves all aa.com cookies.

    Args:
        account: Account name to associate with the saved cookies. Default: "default".
        port: Chromium remote-debugging port (default 9224, matches aa-auth-browser).
    """
    info = extract_session_from_browser(account, port=port)
    _evict(account)
    return json.dumps(info, indent=2)


@mcp.tool()
def get_account_summary(account: str = "") -> str:
    """Member info: name, AAdvantage #, mile balance, status, cobranded card, business memberships.

    Args:
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).member_information(), indent=2)


@mcp.tool()
def get_loyalty_points_progress(account: str = "") -> str:
    """Elite status progress — Loyalty Points YTD + last-year EQDs/EQMs/EQSs + tier thresholds.

    Args:
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).progress_qualification(), indent=2)


@mcp.tool()
def get_profile_details(account: str = "") -> str:
    """Detailed profile: DOB, partners, sales city, mile expiration, million-miler stats.

    Args:
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).profile_details(), indent=2)


@mcp.tool()
def get_mile_activity(
    from_date: str = "",
    to_date: str = "",
    starting_index: int = 0,
    page_size: int = 20,
    search_string: str = "",
    sort_direction: str = "desc",
    account: str = "",
) -> str:
    """AAdvantage mile / loyalty-point transaction history.

    Returns activity cards with date, type (Flight/Bank/Promo/Misc), miles earned/used,
    loyalty points, partner code, and transaction description.

    Args:
        from_date: YYYY-MM-DD inclusive (default: 180 days ago).
        to_date: YYYY-MM-DD inclusive (default: today).
        starting_index: 0-based offset for pagination.
        page_size: Max records (server caps ~50).
        search_string: Free-text filter on transaction description.
        sort_direction: "asc" or "desc" by activity_date.
        account: Account name (optional, uses default).
    """
    if not from_date or not to_date:
        d_from, d_to = default_activity_window()
        from_date = from_date or d_from
        to_date = to_date or d_to

    return json.dumps(
        _get_api(account).member_activity(
            from_date=from_date,
            to_date=to_date,
            starting_index=starting_index,
            page_size=page_size,
            search_string=search_string,
            sort_direction=sort_direction,
        ),
        indent=2,
    )


@mcp.tool()
def get_upcoming_trips(account: str = "") -> str:
    """Upcoming flight reservations with record locators.

    Args:
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).upcoming_trips(), indent=2)


@mcp.tool()
def get_flight_credits(locale: str = "en_US", account: str = "") -> str:
    """Flight credits (single-passenger ticket credits from cancelled flights).

    Args:
        locale: Locale code (default en_US).
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).flight_credits(locale=locale), indent=2)


@mcp.tool()
def get_trip_credits(locale: str = "en_US", account: str = "") -> str:
    """Trip credits (multi-passenger / itinerary-level credits).

    Args:
        locale: Locale code (default en_US).
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).trip_credits(locale=locale), indent=2)


@mcp.tool()
def get_partner_offers(account: str = "") -> str:
    """Partner offers / promotions on the dashboard.

    Args:
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).partner_offers(), indent=2)


@mcp.tool()
def get_notifications(account: str = "") -> str:
    """Account notifications panel.

    Args:
        account: Account name (optional, uses default).
    """
    return json.dumps(_get_api(account).notifications(), indent=2)


def main() -> None:
    if "--setup" in sys.argv:
        print("AA MCP Server — read-only AAdvantage")
        print(f"Accounts: {list_accounts() or '(none)'}")
        print(f"Default: {get_default_account() or '(none)'}")
        print()
        print("To authenticate:")
        print("  1. aa-auth-browser <account>           # launches Chromium")
        print("  2. log into aa.com in that browser")
        print("  3. aa-mcp-server --extract-session <account>")
        return

    if "--extract-session" in sys.argv:
        idx = sys.argv.index("--extract-session")
        if idx + 1 >= len(sys.argv):
            print(
                "Usage: aa-mcp-server --extract-session <ACCOUNT_NAME> [--port N]",
                file=sys.stderr,
            )
            sys.exit(1)
        account = sys.argv[idx + 1]
        port = DEFAULT_PORT
        if "--port" in sys.argv:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        info = extract_session_from_browser(account, port=port)
        print(json.dumps(info, indent=2))
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
