"""American Airlines AAdvantage MCP Server — read-only, multi-account."""

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP

from aa_mcp_server.api import AAAPI, default_activity_window
from aa_mcp_server.auth import (
    AASession,
    extract_session_from_browser,
    extract_session_from_browser_async,
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
async def save_session_from_browser(account: str = "default", port: str = "") -> str:
    """Pull cookies from a logged-in Chromium (started by aa-auth-browser) and save them.

    Workflow:
      1. Run `aa-auth-browser <account>` to launch Chromium with --remote-debugging-port.
      2. Log into aa.com in that browser.
      3. Call this tool — it connects to the debug port via CDP and saves all aa.com cookies.

    Args:
        account: Account name to associate with the saved cookies. Default: "default".
        port: Chromium remote-debugging port as a string. Empty/omitted uses the default
              (9224, matches aa-auth-browser). Pass as string to avoid MCP type-coercion
              issues with some clients.
    """
    port_int = int(port) if port else DEFAULT_PORT
    info = await extract_session_from_browser_async(account, port=port_int)
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
def get_reservation_by_locator(
    record_locator: str,
    last_name: str,
    first_name: str,
    account: str = "",
) -> str:
    """Full reservation details by record locator + lead passenger name.

    Returns the complete viewres payload: passengers (with passengerID like "01.01"),
    itinerary slices and segments (flight numbers, times, fare codes, booking codes),
    tickets (one per passenger), cost summary, eligibilityFlags (cancel / change /
    partial-reshop), and eligibleProducts (deep links to change-flow URLs).

    The underlying endpoint validates first+last name against the lead passenger on
    the PNR, so both are required even when authenticated.

    Args:
        record_locator: 6-character PNR (e.g., "UHJHHT").
        last_name: Lead passenger last name (e.g., "Mohr").
        first_name: Lead passenger first name (e.g., "Alexander").
        account: Account name (optional, uses default).
    """
    return json.dumps(
        _get_api(account).reservation_detail(record_locator, first_name, last_name),
        indent=2,
    )


@mcp.tool()
def search_change_flights(
    record_locator: str,
    last_name: str,
    first_name: str,
    departure_date: str,
    origin_airport: str,
    destination_airport: str,
    slice_index: int = 0,
    carousel_days: bool = True,
    account: str = "",
) -> str:
    """Search alternative flights for an existing reservation (the "change flight" flow).

    Returns the full reshop payload: a ±6-day price carousel around departure_date,
    plus up to 40 flight options with per-cabin pricing (flightCells contain fare,
    netPrice = delta vs paid, cabinType, fareType, changeType, seatsLeft).

    The endpoint allows changing origin city, destination city, and date in a single
    query — AA validates against the original fare rules and either returns SUCCESS
    or an error. Negative netPrice means you'd be issued a travel credit; positive
    means additional payment due. Change fees are usually $0 (waived on non-Basic-
    Economy fares).

    PRICING SCOPE: All numbers are TOTAL for every passenger on the PNR. This
    endpoint does not support per-passenger pricing. To change only some passengers
    on a multi-pax booking, the PNR typically has to be split first via an agent
    (the `divideEligible` flag on the reservation indicates whether the self-serve
    split is allowed; partial reshop without a split usually requires a phone agent).

    Args:
        record_locator: 6-character PNR (e.g., "UHJHHT").
        last_name: Lead passenger last name.
        first_name: Lead passenger first name.
        departure_date: New departure date (YYYY-MM-DD).
        origin_airport: New origin airport (3-letter IATA — can differ from original).
        destination_airport: New destination airport (3-letter IATA — can differ from original).
        slice_index: Which slice of the reservation to change (0 = first; matters for round-trips).
        carousel_days: Include the ±6-day min-price grid in the response (default True).
        account: Account name (optional, uses default).
    """
    return json.dumps(
        _get_api(account).reshop_cheapest(
            record_locator,
            first_name,
            last_name,
            departure_date,
            origin_airport,
            destination_airport,
            slice_index=slice_index,
            carousel_days=carousel_days,
        ),
        indent=2,
    )


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
