# Reverse-proxy subpaths / 反向代理子路径

PanWatch can run at `/`, `/panwatch/`, or a nested path such as `/apps/panwatch/`.
Set the public path at **runtime**; the same frontend build/container image works
for each path. No root-level `/api`, `/assets`, `/login`, `/sw.js`, or `/mcp`
location needs to be exposed on the proxy.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PANWATCH_BASE_PATH` | empty (same as `/`) | Public path, e.g. `/apps/panwatch`. One final slash is accepted. |
| `PANWATCH_PROXY_MODE` | `preserve` | `preserve`: upstream requests retain the prefix; `strip`: the proxy removes it. |
| `FORWARDED_ALLOW_IPS` | Uvicorn's loopback default | Comma-separated trusted proxy IPs, as seen by the backend; required for HTTPS termination at a non-loopback proxy. |

Install the updated requirements when upgrading from an older source checkout:
this feature requires FastAPI >=0.110.1 (Starlette >=0.37.2) for canonical ASGI
root-path routing. Container builds install the requirements automatically.

Restart the server/container after changing these variables. Use ASCII path
segments made of letters, digits, `.`, `_`, `~`, and `-`; empty segments, `.`/`..`,
percent encodings, URLs, whitespace, query strings, fragments and backslashes
are rejected at startup. These settings are explicit: `X-Forwarded-Prefix`,
`X-Script-Name`, `Forwarded`, and other client headers do not select a prefix or
mode. Do not also use Uvicorn `--root-path` or a second ASGI prefix wrapper.

保留路径与剥离路径是两种不同配置，不能混用。默认 `preserve` 模式会拒绝没有前缀的
请求；`strip` 模式的容器内部仍用 `/api/...`，但浏览器始终访问带前缀的公开 URL。
反向代理应只公开该前缀，容器端口应绑定 loopback 或受信任的私有网络。

## Trusted proxy headers for HTTPS / HTTPS 代理信任配置

When Caddy terminates HTTPS and forwards HTTP to PanWatch, it preserves the public
`Host` and sets `X-Forwarded-Proto`. Uvicorn only applies forwarded headers from
trusted connection peers. Its default trusts loopback (`127.0.0.1`, plus `::1`
in newer versions), not another container's private IP. Without explicit trust,
MCP's `WWW-Authenticate` metadata URL and metadata `resource` URL will use `http`
even though the public site uses HTTPS.

Set `FORWARDED_ALLOW_IPS` in the **PanWatch process/container environment** to the
proxy's actual source IP as seen by PanWatch. For example, if that peer is
`10.89.0.2`:

```sh
FORWARDED_ALLOW_IPS=10.89.0.2 python server.py
# For the container command below, add: -e FORWARDED_ALLOW_IPS=10.89.0.2
```

Replace this illustrative address with your proxy's peer IP. A Caddy upstream
name such as `panwatch:8000` identifies the destination; it is not the proxy's
source IP, and DNS names are not resolved by this trust setting. Container port
forwarding/NAT can also change the peer seen by PanWatch, even when Caddy connects
to a published `127.0.0.1` port on the host. Keep the proxy address stable and
recheck it after network changes. Prefer specific IPs; do not use `*` or trust a
whole shared/private subnet containing untrusted workloads. A private address
alone does not establish trust. Restrict backend access to the intended proxy.

`python server.py` uses Uvicorn's existing environment setting; putting it only
in PanWatch's application `.env` does not export it to Uvicorn. With the Uvicorn
CLI, the equivalent is `--proxy-headers --forwarded-allow-ips=10.89.0.2`.
This does not select a path prefix or make `Forwarded`, `X-Forwarded-Host`,
`X-Forwarded-Prefix`, or `X-Script-Name` authoritative.

跨容器 HTTPS 反代必须显式信任后端实际看到的代理源 IP；仅加入私有网络还不够。
请将此变量传入 PanWatch 进程／容器，勿使用 `*`，也勿信任包含不可信容器的整个网段。

## Caddy: preserve the prefix (recommended)

For an IP-based deployment, this example uses plain HTTP deliberately. Replace
`192.0.2.10` (a documentation-only address) with your server IP. If you have a
domain, use the domain as the site address to enable Caddy's normal HTTPS flow.
Do not send credentials over an untrusted plain-HTTP network.

Container environment:

```sh
PANWATCH_BASE_PATH=/apps/panwatch
PANWATCH_PROXY_MODE=preserve
```

Caddyfile (Caddy on the same host as the published container port):

```caddyfile
http://192.0.2.10 {
    redir /apps/panwatch /apps/panwatch/{?query} 308
    handle /apps/panwatch/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        respond "Not found" 404
    }
}
```

`handle` preserves the complete request path. In this mode the backend also
redirects its exact bare prefix to the trailing-slash form, preserving the query
string. Both Caddy examples use `{?query}` to retain the original query string
(including its `?` only when present). Caddy automatically flushes
`text/event-stream` responses immediately;
no special flush override is needed. Do not configure `response_buffers` or add
another buffering proxy between Caddy and PanWatch. Keeping the default also
avoids the negative flush interval's changed backend-disconnect behavior. The app currently uses
fetch-based SSE, not WebSockets; this path adapter also preserves ASGI WebSocket
routing if a WebSocket endpoint is added later.

## Caddy: strip the prefix

Use this alternative only when the proxy explicitly strips the prefix:

```sh
PANWATCH_BASE_PATH=/apps/panwatch
PANWATCH_PROXY_MODE=strip
```

```caddyfile
http://192.0.2.10 {
    redir /apps/panwatch /apps/panwatch/{?query} 308
    handle_path /apps/panwatch/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        respond "Not found" 404
    }
}
```

Here `/apps/panwatch/api/auth/status` reaches the backend as `/api/auth/status`.
The backend reconstructs the canonical public ASGI path and root path; generated
links and Swagger/OpenAPI still refer to `/apps/panwatch/...`. The proxy's bare
prefix redirect is required because `handle_path /apps/panwatch/*` does not match
`/apps/panwatch` itself. Do not publish the strip-mode backend port to the public
network as a substitute for the proxy.

## Podman example

Use the image you built from this branch, or its eventual published version.
The example does not change your existing authentication, data or timezone setup:

```sh
podman run -d --name panwatch \
    -p 127.0.0.1:8000:8000 \
    -e PANWATCH_BASE_PATH=/apps/panwatch \
    -e PANWATCH_PROXY_MODE=preserve \
    -e TZ=Asia/Shanghai \
    -v ./data:/app/data:Z \
    your-panwatch-image:tag
```

The image health check automatically uses the correct internal path for the
selected mode. If Caddy runs in another container, use a shared private Podman
network and the PanWatch container's network name instead of `127.0.0.1`; do not
assume one container's loopback reaches another container. For HTTPS, also set
`FORWARDED_ALLOW_IPS` as described above; joining the network does not configure
Uvicorn's trust list.

## Verification and migration

Open `http://192.0.2.10/apps/panwatch/`, then directly open/reload
`/apps/panwatch/login` and a valid deep link, for example
`/apps/panwatch/assistant/123` for an existing conversation. Check browser Network:

- JS/CSS, icons, manifests and service-worker registration stay under the prefix.
- Login and API requests use `/apps/panwatch/api/...`.
- Notifications/assistant SSE streams remain under that API path and update live.
- PDF downloads and logout redirects remain under the prefix.
- `/apps/panwatch/docs`, `/apps/panwatch/openapi.json`, and
  `/apps/panwatch/.well-known/oauth-protected-resource` are reachable.
- The MCP endpoint shown in settings is `/apps/panwatch/mcp`.
  Its 401 response advertises the absolute, prefixed metadata URL in
  `WWW-Authenticate: Bearer resource_metadata="..."`. Clients should follow that
  challenge rather than require an origin-root `/.well-known/` route. This is
  discovery for the existing PAT authentication, not an OAuth authorization server.
- Through the public HTTPS proxy, both that challenge URL and metadata `resource`
  must begin with the public `https://` origin and contain the prefix exactly once.
  If either advertises `http://`, check the backend's actual proxy peer and
  `FORWARDED_ALLOW_IPS`; do not solve it by trusting every client.
- With preserve mode, unprefixed backend `/api/health` returns 404. With strip
  mode, test the public proxy URL instead: only that URL should be exposed.

The server injects one validated base into `index.html`; React Router uses it as
its basename, while API/direct-download/SSE requests share `publicUrl()`.
Manifests use relative `start_url`, `scope`, and icon URLs; they omit `id` so the
browser uses the resolved `start_url` as the distinct identity of each prefix.
Using the same relative `id` in each deployment would resolve against the origin
and merge these installations. Service-worker caches are isolated by public path
and limited to the public root shell, known
PWA files and bundled assets. They never delete another app's caches or cache
API/auth/protocol responses, arbitrary deep-link navigations, or other nested
applications. Offline deep-link navigation is not supported. Local development with Vite remains at `/`;
use a built frontend with the Python server when testing a subpath.

If this origin previously hosted PanWatch at `/`, an already-installed old root
service worker may control the first load of the new path. Unregister that old
PanWatch worker and remove its old `panwatch-v2` cache in browser developer tools
(or clear this site's data) before migrating; this also signs you out. Update
bookmarks and reinstall the PWA if its old installation starts at `/`. If you
use notification analysis links, also update and save `panwatch_base_url` in
**Settings → System** to the complete public URL, for example
`https://your-host/apps/panwatch` (or the matching trusted-network HTTP URL).
The saved database setting takes precedence over `PANWATCH_BASE_URL`: changing
that environment variable or restarting does not replace an existing saved
value, including an empty one. `PANWATCH_BASE_URL` is only an initial default
used when Settings first creates a missing setting. On a fresh installation,
open Settings to initialize it and verify the saved value before relying on
notification links. This separate setting expects a full URL and is not
automatically prefixed, so an old root URL continues producing old root links.
Other applications' workers/caches are not removed automatically. Switching between
different prefixes on one origin does not create a browser security boundary;
use separate origins for mutually untrusted apps or independent users.

OIDC is a separate feature branch. When combining it, the path component of its
configured `OIDC_PUBLIC_URL` must match `PANWATCH_BASE_PATH`; register the callback
under that same prefix. This branch does not enable or configure an identity
provider and makes no authentication-policy changes.

References: [Caddy handle_path](https://caddyserver.com/docs/caddyfile/directives/handle_path),
[Caddy query placeholders](https://caddyserver.com/docs/caddyfile/concepts#placeholders),
[Caddy streaming](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#streaming),
[Uvicorn proxy-header settings](https://www.uvicorn.org/settings/#http),
[FastAPI behind a proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/),
[manifest identity](https://www.w3.org/TR/appmanifest/#id-member), and
[RFC 9728 discovery challenges](https://www.rfc-editor.org/rfc/rfc9728.html#section-5).
