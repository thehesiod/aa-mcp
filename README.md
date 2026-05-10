# aa-mcp-server

An MCP server that gives Claude (or any MCP-compatible LLM) **read-only** access to your American Airlines AAdvantage account — mile balance, Loyalty Points progress, transaction history, upcoming trips, travel credits, and partner offers.

This is **not** a browser-automation wrapper. The server discovers and calls aa.com's underlying JSON APIs directly using session cookies extracted from a Chromium login. All HTTP traffic uses [`curl_cffi`](https://github.com/lexiforest/curl_cffi) with Chrome TLS impersonation to satisfy aa.com's Akamai Bot Manager.

## Tools

| Tool | Endpoint | Returns |
| - | - | - |
| `get_account_summary` | `/loyalty/api/member-information` | Name, AAdvantage #, mile balance, status, cobranded card, business memberships |
| `get_loyalty_points_progress` | `/loyalty/api/progress-qualification` | LP YTD, last-year totals, tier thresholds, next-status delta |
| `get_profile_details` | `/api/loyalty/.../profile` | DOB, partners list, sales city, mile expiration, million-miler stats |
| `get_mile_activity` | `/api/loyalty/.../memberActivity` | Mile/LP transaction history (date range, paginated, searchable) |
| `get_upcoming_trips` | `/loyalty/api/upcoming-trips` | Reservations with record locators |
| `get_reservation_by_locator` | `/manage-reservation/viewres/api/reservation` | Full reservation: segments, passengers, tickets, costs, change/cancel eligibility (requires lead-passenger name) |
| `search_change_flights` | `/manage-reservation/reshop/api/reshop/cheapest` | Alternative flights for a reservation with a ±6-day price carousel, per-cabin pricing (`netPrice` = delta vs paid). Accepts origin/destination/date changes; pricing is total for all pax (no per-passenger split) |
| `get_flight_credits` | `/api/loyalty/travelCredits/flightCredit/details` | Single-passenger ticket credits |
| `get_trip_credits` | `/api/loyalty/travelCredits/tripCredit/details` | Multi-passenger / itinerary credits |
| `get_partner_offers` | `/loyalty/api/partnerOffers` | Dashboard partner promotions |
| `get_notifications` | `/loyalty/api/notifications` | Account notifications |
| `check_auth_status` | — | Saved-session info: AA #, token expiry, cookie count |
| `save_session_from_browser` | CDP | Pulls cookies from a logged-in Chromium and persists them |

## Setup

```bash
pip install aa-mcp-server
```

### One-time auth

aa.com is fronted by Akamai Bot Manager — you can't log in via headless requests. The flow:

1. Launch a real Chromium with a persistent profile and a remote-debugging port:
   ```bash
   aa-auth-browser              # default account
   aa-auth-browser personal     # named account
   ```
2. Log into aa.com (and complete 2FA if prompted) in the window that opens.
3. Copy the cookies into the MCP store:
   ```bash
   aa-mcp-server --extract-session default
   ```
   Or call the `save_session_from_browser` MCP tool from Claude.

The cookies live in `~/.aa-mcp/accounts/<account>/session.json`. The chromium profile lives in `~/.aa-mcp/chrome-profiles/<account>/`. To refresh after expiry, relaunch `aa-auth-browser` (the saved profile auto-refreshes the access_token cookie when you visit any aa.com page) and re-run `--extract-session`.

`AA_MCP_CHROMIUM=<path-to-chrome.exe>` overrides the auto-discovery if your Chromium is somewhere unusual.

### Multi-account

Every tool takes an optional `account` parameter. Omit it to use the default account.

```python
get_account_summary()                # default
get_account_summary(account="spouse")
```

## Running

```bash
aa-mcp-server                        # stdio transport
aa-mcp-server --setup                # show account status & setup hints
aa-mcp-server --extract-session NAME # save cookies from running Chromium
```

Add to your MCP client config (e.g. Claude Desktop):

```json
{
  "mcpServers": {
    "aa": {
      "command": "aa-mcp-server"
    }
  }
}
```

## Limitations

- **Read-only.** No booking, no award redemption, no profile edits.
- **Cookie expiry.** The `access_token` JWT lives ~45 min; the `refresh_token` cookie typically ~30 days. Visiting any aa.com page in the saved Chromium profile silently refreshes both. After a long gap, re-extract.
- **GraphQL coverage.** Only the `GetCustomer` persisted query is wired up. Expanding requires capturing additional sha256Hashes from the browser bundle — easy but a per-feature task.
- **One region tested.** All testing has been on US-locale aa.com. International locales may require different `referer` paths.

## License

MIT

mcp-name: io.github.thehesiod/aa
