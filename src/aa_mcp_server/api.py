"""American Airlines aa.com API client — cookie-based auth, curl_cffi chrome impersonation."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests as curl_requests
from yarl import URL

from aa_mcp_server.auth import AASession

# aa.com is fronted by Akamai Bot Manager. Without a chrome JA3/JA4 fingerprint,
# requests get TLS-dropped before they reach the application.
_IMPERSONATE = "chrome131"

logger = logging.getLogger(__name__)

BASE = "https://www.aa.com"

# /loyalty/api/* endpoints — XSRF-token-protected, JSON, cookie-authenticated.
LOYALTY_MEMBER_INFO = f"{BASE}/loyalty/api/member-information"
LOYALTY_PROGRESS = f"{BASE}/loyalty/api/progress-qualification"
LOYALTY_UPCOMING_TRIPS = f"{BASE}/loyalty/api/upcoming-trips"
LOYALTY_NOTIFICATIONS = f"{BASE}/loyalty/api/notifications"
LOYALTY_PARTNER_OFFERS = f"{BASE}/loyalty/api/partnerOffers"
LOYALTY_BANNER_ADS = f"{BASE}/loyalty/api/bannerAds"
LOYALTY_ACCESS_LEVEL = f"{BASE}/loyalty/access-level"

# /api/loyalty/* endpoints — same cookie auth, slightly different shape.
TRAVEL_CREDITS_FLIGHT = f"{BASE}/api/loyalty/travelCredits/flightCredit/details"
TRAVEL_CREDITS_TRIP = f"{BASE}/api/loyalty/travelCredits/tripCredit/details"
MEMBER_ACTIVITY = f"{BASE}/api/loyalty/miles/transaction/orchestrator/memberActivity"
PROFILE_DETAILS = f"{BASE}/api/loyalty/miles/transaction/orchestrator/profile"
PROMO_RIBBONS = f"{BASE}/api/loyalty/br/retrieve/account"

# /manage-reservation/viewres/api/* — guest-lookup-style endpoints (need first+last name).
RESERVATION_DETAIL = f"{BASE}/manage-reservation/viewres/api/reservation"

# /manage-reservation/reshop/api/* — change-flight (reshop) endpoints. Stateless; require
# the encrypted `data` blob from eligibleProducts[CHANGE].outletUrl on the reservation.
RESHOP_CHEAPEST = f"{BASE}/manage-reservation/reshop/api/reshop/cheapest"

GRAPHQL_ENDPOINT = f"{BASE}/services/graphql"

# Persisted-query hash for the GetCustomer operation discovered in browser bundle.
GRAPHQL_GET_CUSTOMER_HASH = (
    "73f3ea543fffb8f17183ee4d3adcb824c1bd91b1b5727a524af33499d2d863eb"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


class AAAuthExpired(RuntimeError):
    """Raised when the saved cookies no longer authenticate."""


class AAAPI:
    """Read-only client for aa.com AAdvantage endpoints."""

    def __init__(self, session: AASession) -> None:
        self._session = session

    def _headers(self, *, json_body: bool = False, referer_path: str = "/aadvantage-program/profile/account-summary") -> dict[str, str]:
        cookies = self._session.cookies_dict()
        xsrf = cookies.get("XSRF-TOKEN", "")
        h = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": BASE,
            "Referer": f"{BASE}{referer_path}",
            "User-Agent": _USER_AGENT,
            "sec-ch-ua": '"Not/A)Brand";v="99", "Chromium";v="148"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if xsrf:
            h["x-xsrf-token"] = xsrf
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        extra_headers: dict | None = None,
        referer_path: str = "/aadvantage-program/profile/account-summary",
    ) -> dict | list:
        headers = self._headers(json_body=json_body is not None, referer_path=referer_path)
        if extra_headers:
            headers.update(extra_headers)

        resp = curl_requests.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            cookies=self._session.cookies_dict(),
            impersonate=_IMPERSONATE,
            timeout=30,
        )
        if resp.status_code in (401, 403):
            raise AAAuthExpired(
                f"AA API {url} returned {resp.status_code}. "
                f"Re-run aa-auth-browser, log in (or reload aa.com), then "
                f"call save_session_from_browser to refresh cookies."
            )
        resp.raise_for_status()
        return resp.json()

    # ---- /loyalty/api/* ----

    def member_information(self) -> dict:
        """Profile + balance + status + cobranded card + business membership summary."""
        return self._request("GET", LOYALTY_MEMBER_INFO)  # type: ignore[return-value]

    def progress_qualification(self) -> dict:
        """Loyalty Points elite-tier progress (current YTD + last year totals)."""
        return self._request("GET", LOYALTY_PROGRESS)  # type: ignore[return-value]

    def upcoming_trips(self) -> dict:
        """Upcoming flight reservations with record locators."""
        return self._request("GET", LOYALTY_UPCOMING_TRIPS)  # type: ignore[return-value]

    def notifications(self) -> dict:
        """Account notifications panel data."""
        return self._request("GET", LOYALTY_NOTIFICATIONS)  # type: ignore[return-value]

    def partner_offers(self) -> dict:
        """Partner offers / promotions visible on the account dashboard."""
        return self._request("GET", LOYALTY_PARTNER_OFFERS)  # type: ignore[return-value]

    def banner_ads(self) -> dict:
        """Promotional banner ads (low-value but cheap)."""
        return self._request("GET", LOYALTY_BANNER_ADS)  # type: ignore[return-value]

    def access_level(self) -> dict:
        """Account access status code (e.g. {'status': '2'})."""
        return self._request("GET", LOYALTY_ACCESS_LEVEL)  # type: ignore[return-value]

    # ---- /api/loyalty/* ----

    def flight_credits(self, locale: str = "en_US") -> dict:
        """Flight credits (single-passenger non-refundable ticket credits)."""
        return self._request(  # type: ignore[return-value]
            "GET",
            TRAVEL_CREDITS_FLIGHT,
            params={"locale": locale},
        )

    def trip_credits(self, locale: str = "en_US") -> dict:
        """Trip credits (multi-passenger / itinerary-level credits)."""
        return self._request(  # type: ignore[return-value]
            "GET",
            TRAVEL_CREDITS_TRIP,
            params={"locale": locale},
        )

    def member_activity(
        self,
        from_date: str,
        to_date: str,
        starting_index: int = 0,
        page_size: int = 20,
        sort_by: str = "activity_date",
        sort_direction: str = "desc",
        search_category: str | None = None,
        search_string: str = "",
        page_name: str = "your_activity",
    ) -> dict:
        """AAdvantage mile/loyalty-point transaction history.

        Args:
            from_date: YYYY-MM-DD inclusive
            to_date: YYYY-MM-DD inclusive
            starting_index: 0-based offset for pagination
            page_size: max records to return (server caps around 50)
            sort_by: "activity_date" (others may exist; not enumerated)
            sort_direction: "asc" or "desc"
            search_category: partner category filter (None = all)
            search_string: free-text filter (empty = all)
            page_name: "your_activity" (full) or "overview" (3-card preview)
        """
        body: dict[str, Any] = {
            "activityFromDate": from_date,
            "activityToDate": to_date,
            "startingRecordIndex": starting_index,
            "numberOfRecordsToFetch": page_size,
            "searchCategory": search_category,
            "searchString": search_string,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        }
        if page_name:
            body["pageName"] = page_name
        return self._request(  # type: ignore[return-value]
            "POST",
            MEMBER_ACTIVITY,
            json_body=body,
            referer_path="/aadvantage-program/profile/recentActivity",
        )

    def profile_details(self) -> dict:
        """Detailed profile: DOB, partners list, sales city, mile expiration, million-miler stats."""
        return self._request(  # type: ignore[return-value]
            "POST",
            PROFILE_DETAILS,
            json_body={},
            referer_path="/aadvantage-program/profile/recentActivity",
        )

    def promo_ribbons(self) -> dict:
        """Promotional ribbons/badges shown on account summary."""
        return self._request("GET", PROMO_RIBBONS)  # type: ignore[return-value]

    # ---- /manage-reservation/viewres/api/* ----

    def reservation_detail(self, record_locator: str, first_name: str, last_name: str) -> dict:
        """Full reservation details — segments, passengers, tickets, costs, eligibility flags.

        first_name/last_name must match the lead passenger on the booking; the endpoint
        is a guest-lookup-style POST that validates both even when authenticated.
        """
        referer_path = str(
            URL("/reservation/selectReservationSubmit.do").with_query(
                {"recordLocator": record_locator}
            )
        )
        body = {
            "recordLocator": record_locator,
            "firstName": first_name,
            "lastName": last_name,
            "fromSource": True,
            "locale": "en_US",
        }
        return self._request(  # type: ignore[return-value]
            "POST",
            RESERVATION_DETAIL,
            json_body=body,
            referer_path=referer_path,
        )

    # ---- /manage-reservation/reshop/api/* ----

    def _change_flight_data_blob(
        self, record_locator: str, first_name: str, last_name: str
    ) -> str:
        """Extract the encrypted `data` blob from eligibleProducts[CHANGE].outletUrl.

        The blob is server-issued state tying a reshop session to a specific PNR +
        ticket combo. Every /reshop/api/* call requires it. Fresh on every viewres call.
        """
        res = self.reservation_detail(record_locator, first_name, last_name)
        for product in res.get("eligibleProducts", []):
            if product.get("name") != "CHANGE":
                continue
            params = parse_qs(urlparse(product.get("outletUrl", "")).query)
            if data := params.get("data"):
                return data[0]
        raise RuntimeError(
            f"No CHANGE eligibleProduct on PNR {record_locator} — the booking may not be changeable."
        )

    def reshop_cheapest(
        self,
        record_locator: str,
        first_name: str,
        last_name: str,
        departure_date: str,
        origin_airport: str,
        destination_airport: str,
        slice_index: int = 0,
        carousel_days: bool = True,
    ) -> dict:
        """Search alternative flights for one slice of a reservation.

        Returns the AA "reshop cheapest" payload: a date carousel (±6 days around
        departure_date with min price per date) plus up to 40 flight options, each
        with flightCells covering the cabin/fare buckets the customer is eligible for.

        Prices are TOTAL for all passengers on the PNR — the endpoint does not
        support per-passenger pricing. AA appears to accept origin and destination
        changes (not just dates) for at least some fare rules; the server returns
        SUCCESS or an error depending on whether the change is allowed.

        Gotcha: `carouselDays.minPrice` is advisory and may not match `flightCells.netPrice`
        for the same date. We've seen carousel values lower than any cell's netPrice on
        the same query, suggesting the carousel reflects fare buckets the customer isn't
        actually eligible to book. Treat `flightCells.netPrice` as authoritative.

        Fetches a fresh `data` blob from eligibleProducts[CHANGE].outletUrl on every
        call (the blob is short-lived; refetch each time).
        """
        data_blob = self._change_flight_data_blob(record_locator, first_name, last_name)
        referer_path = str(
            URL("/manage-reservation/reshop/v2/change-flights").with_query(
                {
                    "recordLocator": record_locator,
                    "data": data_blob,
                    "from": "change_res",
                }
            )
        )
        body = {
            "transactionId": str(uuid.uuid4()).upper(),
            "clientId": "AACOM_ChangeRes",
            "searchCriteria": {
                "flightDetails": [
                    {
                        "departureDate": departure_date,
                        "originAirportCode": origin_airport,
                        "destinationAirportCode": destination_airport,
                        "selectedSliceForChange": True,
                        "sliceIndex": slice_index,
                        "flown": False,
                    }
                ]
            },
            "currentSearchSlice": 0,
            "originalReservationTotalSlices": "1",
            "recordLocator": record_locator,
            "tripType": "ONEWAY",
            "data": data_blob,
            "carouselDays": carousel_days,
        }
        return self._request(  # type: ignore[return-value]
            "POST",
            RESHOP_CHEAPEST,
            json_body=body,
            referer_path=referer_path,
        )

    # ---- GraphQL ----

    def graphql_get_customer(self) -> dict:
        """GraphQL persisted query — slim customer view (name, balance, points, DOB)."""
        body = {
            "operationName": "GetCustomer",
            "variables": {"advantageNumber": "some-default-advantage-number"},
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": GRAPHQL_GET_CUSTOMER_HASH,
                }
            },
        }
        extras = {
            "apollographql-client-name": "promotions-aacom",
            "apollographql-client-version": "1.0.1",
            "x-transactionid": str(uuid.uuid4()),
        }
        return self._request(  # type: ignore[return-value]
            "POST",
            GRAPHQL_ENDPOINT,
            json_body=body,
            extra_headers=extras,
        )


def default_activity_window() -> tuple[str, str]:
    """Default 6-month window ending today, in YYYY-MM-DD format."""
    today = datetime.now().date()
    start = today - timedelta(days=180)
    return start.isoformat(), today.isoformat()
