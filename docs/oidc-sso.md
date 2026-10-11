# Optional single-owner OIDC single sign-on

[简体中文](oidc-sso.zh-CN.md)

OIDC is disabled by default. Existing password login continues unchanged while it is disabled. Enabling OIDC does **not** add separate users or portfolios: every explicitly permitted identity gets the same owner's full access. Do not allow a provider-wide group or arbitrary provider users.

## Configure the provider and application

Create an OpenID Connect application at your chosen provider. This implementation uses discovery, Authorization Code flow, S256 PKCE and asymmetric signed ID tokens. Use a confidential web client when your provider supports it. No provider is preselected or configured by this branch.

Register exactly this redirect URI, replacing the example origin with your own:

```
https://portfolio.example.com/api/auth/oidc/callback
```

Then set environment variables in the PanWatch container (Podman/Docker `--env-file` or Compose `environment`), or its working directory's untracked `.env`. Process environment takes precedence. Restart PanWatch after changes. Keep client secrets out of Git, frontend variables, logs and screenshots.

```dotenv
OIDC_ENABLED=true
OIDC_ISSUER=https://identity.example.com
OIDC_CLIENT_ID=panwatch
OIDC_CLIENT_SECRET=replace-with-your-provider-client-secret
OIDC_PUBLIC_URL=https://portfolio.example.com
OIDC_ALLOWED_SUBJECTS=["exact-owner-subject-from-provider"]
```

`OIDC_ISSUER` must exactly match discovery's `issuer`, including any tenant path or trailing slash. Find the owner's exact stable `sub` through your provider's admin tools or documented claim viewer. The allowlist is a JSON string array; comparison is exact and scoped to this issuer. Email addresses and `email_verified` are never authorization rules. An empty allowlist or invalid enabled configuration fails closed; startup does not fall back to guest access.

The server must be able to resolve the provider and reach discovery, its token endpoint, and its JWKS endpoint over trusted HTTPS. Discovery endpoints must use the issuer's exact origin by default. Providers that publish endpoints on additional origins require an explicit JSON array, for example:

```dotenv
OIDC_ALLOWED_ENDPOINT_ORIGINS=["https://tokens.identity.example.com","https://keys.identity.example.com"]
```

Only list origins documented and controlled by your trusted identity provider. This list controls server-side requests and authorization redirects. Endpoints containing credentials, fragments, query strings or ambiguous paths are rejected; redirects are not followed. Configuring a trusted internal identity provider is allowed, but provider configuration is an administrator trust boundary, not user input. Set up DNS and a trusted certificate for a private provider rather than disabling certificate validation.

## Options

| Variable | Default | Meaning |
| --- | --- | --- |
| `OIDC_ENABLED` | `false` | Enable OIDC; requires all mandatory configuration above |
| `OIDC_TOKEN_AUTH_METHOD` | `client_secret_basic` | Also supports `client_secret_post` or `none` (public client with PKCE) |
| `OIDC_ALLOW_PASSWORD_LOGIN` | `false` | Explicitly retain an already-configured local password as a fallback |
| `OIDC_SESSION_HOURS` | `12` | Absolute local session lifetime, integer 1–168 hours |
| `OIDC_ALLOWED_ENDPOINT_ORIGINS` | `[]` | Additional explicitly trusted discovery endpoint origins |
| `OIDC_ALLOW_HTTP_LOCALHOST` | `false` | Development only: allow HTTP for literal `localhost`, `127.0.0.1` or `::1` |

SSO-only disables password login, password changes and acceptance of existing password JWTs on the backend. Public account setup is disabled whenever OIDC is enabled, even with password fallback. Configure any desired fallback account before enabling OIDC. Existing password hashes are not deleted or changed. Turning OIDC off re-enables the existing password policy, including the original unconfigured initial state, so first ensure a local password is configured before turning it off on a publicly reachable installation.

The API and app use the same origin. OIDC browser sessions use a Secure, HttpOnly, host-only, SameSite=Lax cookie. A verified `/api/auth/status` response supplies a memory-only CSRF token; authenticated cookie mutations require both that token in `X-CSRF-Token` and an exact configured `Origin`. This includes POST-based SSE. Existing SSE streams recheck their exact session before each emitted chunk and stop sending private data after logout or expiry. CORS permits only the configured application origin when OIDC is enabled. Do not put another untrusted app on the same origin.

## Caddy, Cloudflare Tunnel, LAN and subpaths

`OIDC_PUBLIC_URL` is the one canonical browser-facing HTTPS URL. It is never inferred from Host, X-Forwarded-Host or a callback parameter. Caddy/Cloudflare may terminate HTTPS before a private HTTP connection to PanWatch; that does not require enabling insecure cookies. Prevent direct public access to the backend port, and do not trust forwarded headers from arbitrary clients.

For LAN and tunnel access, use the same canonical HTTPS hostname when signing in. DNS can resolve that hostname to the local Caddy instance on the LAN. An HTTP LAN IP address is not an alternative authenticated origin; normal Secure cookies intentionally do not work there.

For an app served at a prefix, the reverse-proxy-subpath feature must also be present and configured. For example, set `PANWATCH_BASE_PATH=/apps/panwatch` and `OIDC_PUBLIC_URL=https://portfolio.example.com/apps/panwatch`, then register `https://portfolio.example.com/apps/panwatch/api/auth/oidc/callback`. The paths must match. Cookies are scoped to `/apps/panwatch/`, and both session and login-transaction cookie names are namespaced by this canonical path. Each login adds an unpredictable generation suffix to both cookie names; the root and nested deployment namespaces remain distinct. Moving from `/` to a nested path therefore does not let an older ancestor cookie shadow the new session. Upgrading from fixed-name OIDC cookies requires one fresh sign-in at any path. Legacy cookies and sessions cannot authenticate without a generation record and expire normally; initialization only adds the generation table, without altering existing tables. This OIDC branch alone does not add frontend or proxy subpath routing.

Do not cache `/api/auth/*`, including redirects and failures, at the proxy. Responses already send `Cache-Control: no-store`. Redact callback query strings in Caddy, tunnel and application access logs: standard authorization callbacks contain short-lived codes and state. Do not enable HTTP body/debug logging for the provider exchange. The application does not log provider responses, claims, codes, secrets or tokens.

## Sessions, logout and API clients

The server exchanges and validates the ID token and discards provider access/refresh/ID tokens. It verifies signature against discovery JWKS, an explicit asymmetric algorithm allowlist, exact issuer, audience, expiry, issued-at, nonce and authorized-party rules. A ten-minute, one-use login transaction binds state and PKCE to the initiating browser. Provider cancellation, invalid state and errors return generic translated login messages.

The local session is an opaque random ID. Only its hash, CSRF token, owner identity, configuration binding and absolute expiry are persisted in SQLite. Sessions survive a restart, expire without silent refresh, and are rejected if the configured issuer, client ID, public URL or subject authorization no longer matches. Expired rows are cleaned up during login. A successful new login revokes the browser's previous session. Starting or cancelling a retry preserves a session whose valid credential the browser already presents, while superseding older pending or undelivered attempts.

Each login has a server-issued monotonic generation, a random browser binding and a unique session cookie name. Authentication requires the matching binding and session-cookie pair; a missing, expired or corrupted binding requires a fresh sign-in. Callbacks never overwrite a bootstrap binding or another generation's session cookie. The callback's final database write atomically checks that its generation has not been revoked; delayed successful responses cannot replace a newer valid credential or revive an older session after logout. Concurrent first-time starts retain distinct binding cookies and resolve by server generation, independent of response or cookie-header order.

Logout revokes the exact generations evidenced by that request's valid binding/session cookies, without sending cookie deletion headers. A superseded session's original CSRF token and credential hash remain a narrowly scoped logout proof until its original session expiry. This lets logout cancel a retry that has committed but whose cookie has not arrived, without allowing a stale request to revoke a later login it never observed. Such proof never authenticates status, APIs or SSE. Other browsers/devices are independent. When password fallback is explicitly enabled, a valid configured password Bearer token plus the canonical Origin can also cancel only the generations evidenced by that request, including a pending or undelivered first OIDC login. Anonymous requests and password JWTs in SSO-only mode do not grant this authority.

Transactions still expire after ten minutes. Their non-authenticating binding cookies remain for the configured session lifetime plus ten minutes from start, so even a held callback response stays associated with later logout. Generation records and logout proofs have bounded expiry; terminal attempts that never issued a session are retained for at most another ten minutes. Later login/success responses prune only observed obsolete, never-reused cookie names, at most 16 deletions per response; delayed cleanup cannot delete a newer credential. Steady-state login uses a binding/session pair, plus one binding during a retry. Many delayed first-touch responses may temporarily retain extra inert cookies; batched lookup and gradual cleanup let status, logout and new login recover without discarding active evidence. The existing global limit of 128 pending login transactions still applies.

Logout is a CSRF-protected POST that revokes the local session. If another tab signs in and changes the active session, a `csrf_invalid` logout response triggers a fresh, uncached status check and at most one retry with the changed CSRF token. Network failures and other mutations are never automatically replayed; failed logout retains the verified session so the owner can retry. It does not log you out of the identity provider or other applications. The provider may sign you straight back in next time; use its own sign-out controls when leaving a shared computer. Provider-side disablement or logout does not instantly revoke an existing PanWatch session; allowlist removal followed by an application restart rejects that identity immediately. Provider back-channel logout and token refresh are not implemented.

MCP continues to use its existing scoped `pwmcp_` personal access tokens; OIDC is not an OAuth authorization server for MCP. Existing MCP PATs remain valid according to their own scope, expiry and revocation rules. General REST clients can either implement the cookie/status/CSRF flow on the canonical origin or use the existing password bearer-token flow only when explicit password fallback is enabled. Provider ID/access tokens are not PanWatch API bearer tokens.

## Read-only GET and API migration

Cookie CSRF protection depends on GET/HEAD/OPTIONS remaining observational. Integrations using older action-by-GET endpoints must migrate:

- Execute selfchecks with `POST /api/health/selfcheck`; existing `notify_send` and comma-separated `keys` query parameters are unchanged. Only `GET /api/health/selfcheck?list=1` remains read-only. Other GET requests return 405 and `Allow: POST`, including when notification sending is false, because AI probes may spend quota. The frontend already sends POST for both complete and per-item checks.
- Refresh entry candidates with the existing `POST /api/recommendations/entry-candidates/refresh`. `GET /api/recommendations/entry-candidates?refresh=true` returns an explicit migration error without changing candidates. Normal candidate-list GETs remain supported; the frontend SDK no longer exposes a `refresh` option on `listEntryCandidates`, so use `refreshEntryCandidates` followed by a list read.
- `GET /api/assistant/exports/{export_id}` only reads progress/results; it cannot start queued AI generation. Existing creation/retry POSTs and startup recovery own job execution. Polling clients do not need to change.

OIDC cookie POSTs require the verified CSRF header and canonical Origin described above. Explicitly enabled password-bearer clients use the same POST endpoints without cookie CSRF.

## Validation and current boundaries

Automated tests use a disposable signed mock identity provider, never your real provider. They cover successful code exchange, PKCE, issuer/audience/signature/nonce/expiry rejection, browser binding, replay, explicit owner authorization, configuration failures, session expiry/rotation, password compatibility, CSRF including SSE, and logout revocation. Frontend tests cover status-first guards, login errors/retry, stale password tokens and confirmed logout. A real-provider deployment and the user's Orange Pi/Caddy/Cloudflare setup must be verified separately before enabling production SSO.

Protocol references: [OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html), [OAuth Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html), [PyJWT verification API](https://pyjwt.readthedocs.io/en/stable/api.html).

Internet-facing deployments should rate-limit `/api/auth/oidc/login` at the trusted Caddy/Cloudflare edge. The application bounds pending transactions to 128 and expires them after ten minutes; an anonymous client can exhaust that budget and temporarily cause 429 responses. This storage bound is not a substitute for abuse protection, and ongoing flooding can prolong the interruption. No edge or deployment settings are changed automatically.
