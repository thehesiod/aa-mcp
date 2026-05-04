# AA MCP Server

## Architecture

MCP server that talks directly to aa.com's internal JSON APIs using session cookies extracted from a Chromium login. No scraping, no headless browser at runtime — all API calls are pure HTTP via `curl_cffi` with Chrome TLS impersonation. Runs as stdio transport.

```
src/aa_mcp_server/
  server.py         — MCP tool definitions (FastMCP)
  api.py            — AAAPI: typed wrappers around each /loyalty/api/* and /api/loyalty/* endpoint
  auth.py           — AASession: cookie jar persistence + CDP extraction
  auth_browser.py   — aa-auth-browser console script (launches Chromium with --remote-debugging-port)
```

## Key Patterns

### Authentication (PingFederate + Akamai Bot Manager)

- aa.com login is PingFederate-backed (`login.aa.com/loyalty/pf-ws/authn/flows/`). After login, `www.aa.com` gets two cookies:
  - `access_token` — RS256 JWT, ~45 min expiry, `sub` is the AAdvantage number, issued by `https://login.aa.com/loyalty`.
  - `refresh_token` — opaque PingFederate format, ~30 day TTL.
- All AAdvantage APIs are **cookie-authenticated**, not Bearer-authenticated. The JWT just rides along in the cookie jar.
- aa.com is fronted by Akamai Bot Manager. Stdlib HTTP (httpx, requests) gets TLS-dropped at the edge. We use `curl_cffi` with `impersonate="chrome131"` — same pattern as costco-mcp.
- The browser sends a `x-xsrf-token` header on `/loyalty/api/*` calls. Its value mirrors the `XSRF-TOKEN` cookie.

### Multi-account

- Sessions: `~/.aa-mcp/accounts/<name>/session.json` per account.
- Chromium profiles: `~/.aa-mcp/chrome-profiles/<name>/` (separate so cookies don't collide).
- Config (default account + account list) at `~/.aa-mcp/config.json`.
- Every MCP tool takes an optional `account` parameter. Omit to use default.
- `_apis` dict in `server.py` caches `AAAPI` instances; eviction on `save_session_from_browser`.

### Cookie extraction (CDP)

The flow is one-time per ~30 days:

1. `aa-auth-browser <account>` launches the user's Chromium with `--remote-debugging-port=9224 --user-data-dir=~/.aa-mcp/chrome-profiles/<account>`.
2. User logs in once. Profile retains cookies between launches.
3. `save_session_from_browser` (MCP tool) or `aa-mcp-server --extract-session <name>` connects to `http://127.0.0.1:9224/json/version` to discover the WebSocket URL, opens a CDP WebSocket, calls `Network.getAllCookies`, filters to aa.com domains, persists.

We store the **full** cookie set, not just `access_token` — Akamai's bot-detection cookies (`_abck`, `bm_sz`, `bm_sc`, `ak_*`) are part of the session signature. Drop them and 401s start.

### Endpoint families

Two distinct families, both cookie-authenticated:

- **`/loyalty/api/*`** — XSRF-protected (`x-xsrf-token` header required), used by the React SPA at `/aadvantage-program/profile/*`. Members info, progress, trips, notifications.
- **`/api/loyalty/*`** — Same auth model but no XSRF requirement on most. Mile activity, profile details, travel credits, promo ribbons.
- **`/services/graphql`** — Apollo with persisted queries (sha256Hash, no inline query string). Only `GetCustomer` is wired up; adding more = capture the hash from the browser bundle.

### Response format

All tools return JSON-stringified API responses (no formatting). The data is rich and structured — Claude does better filtering/projecting raw JSON than parsing pre-formatted markdown.

## Known Gotchas

### Cookie expiry

Symptom: any tool returns `AAAuthExpired`. The `access_token` cookie's JWT `exp` claim is the ground truth — `check_auth_status` reports it.

Fix: relaunch `aa-auth-browser <account>`. Just opening aa.com in that browser triggers a silent token refresh (Ping rotates `access_token` from `refresh_token`). Then re-run `--extract-session` to copy the new cookies.

If `refresh_token` itself has expired (~30 days idle), the browser will redirect to login — log in again.

### Akamai cookie drift

If `_abck` / `bm_*` cookies fall out of sync, even valid `access_token` requests get 403'd. Re-extract via CDP from the browser; don't try to hand-edit cookies.

### Persisted GraphQL queries

The `GRAPHQL_GET_CUSTOMER_HASH` constant is the sha256 of the GetCustomer operation as of capture. If aa.com rebuilds the bundle and rotates the hash, the call fails with `PersistedQueryNotFound`. Re-capture from the browser's Network panel — look at the `extensions.persistedQuery.sha256Hash` field on a `/services/graphql` POST.

### Account-information page

`/aadvantage-program/profile/account-information` (the contact-info edit page) requires write-scope tokens that the SPA doesn't seem to fetch on first load. Profile read fields are exposed via `profile_details` (POST `/api/loyalty/miles/transaction/orchestrator/profile`) instead — that's what we use.

## Development

```bash
uv run aa-mcp-server                          # run the server
uv run aa-auth-browser personal               # launch chromium for a named account
uv run aa-mcp-server --extract-session personal
uv run aa-mcp-server --setup                  # status / hint
```

### Adding an endpoint

1. Capture the request from the browser Network panel — copy URL, method, body shape, response shape.
2. Add a constant URL + a method on `AAAPI` in `api.py`. Reuse `_request`; pass `referer_path` if the endpoint is checked.
3. Add an `@mcp.tool()` wrapper in `server.py`. Always accept an optional `account: str = ""`.
4. Document the response shape in the docstring (Claude needs hints to make sense of unfamiliar JSON).

### Testing

No test suite checked in. Smoke-test against your own account:

```bash
aa-auth-browser
# log in, then in another terminal:
aa-mcp-server --extract-session default
# launch the server and call check_auth_status / get_account_summary via an MCP client
```

## Release Process

Same flow as costco-mcp / psquare-mcp — PyPI Trusted Publishers + GitHub OIDC for the MCP Registry.

### Cutting a release

1. Bump version in `pyproject.toml` AND `server.json` (`version` + `packages[0].version`). The CI verifies they match the git tag.
2. Commit `chore: bump to X.Y.Z`.
3. `git tag X.Y.Z && git push origin main --tags` (bare semver, no `v` prefix).
4. CI publishes to PyPI, the MCP Registry, and creates a GitHub Release.

### Ownership proof for the MCP Registry

`README.md` ends with the literal line `mcp-name: io.github.thehesiod/aa`. The registry's publisher fetches the published PyPI artifact and looks for that string. Removing it breaks future registry publishes.

## Open Improvement Areas

- **Auto-refresh from refresh_token.** Right now expiry forces a relaunch of Chromium. Calling Ping's `/loyalty/connect/token` directly with `refresh_token` would let us mint new `access_token` JWTs without the browser, but we'd still need fresh Akamai cookies — net unclear whether it saves the user any work.
- **GraphQL coverage.** Only `GetCustomer` is wired. Persisted-query hashes for award search, seat maps, etc. are reachable from the browser bundle.
- **Token scope vs. tool surface.** The captured JWT's scope claim lists `profile_award_miles_read`, `profile_elite_progress_read`, `profile_partners_read`, etc. — every read scope the SPA uses. We could enumerate every endpoint covered by these scopes and add tools for them.
- **Cookie diff after refresh.** When the user re-launches Chromium, only some cookies rotate. A `refresh_session` tool that connects to the existing browser and merges new cookies (instead of full re-extract) would be slightly cheaper.
