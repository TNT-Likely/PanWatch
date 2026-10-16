# 可选的单所有者 OIDC 单点登录

[English](oidc-sso.md)

默认关闭 OIDC，关闭时保留原有密码登录。启用后仍是同一个所有者、同一份持仓和完整权限，**不是多租户账户系统**。只有明确列入名单的身份可以进入；不会自动放行身份提供方的全部用户，也不使用邮箱或 `email_verified` 自动授权。

## 配置

在选定的身份提供方创建 OpenID Connect Web 应用，启用 Authorization Code 和 S256 PKCE。支持 discovery 与非对称签名 ID Token，没有预设或实际启用任何提供方。回调地址必须精确登记为：

```
https://portfolio.example.com/api/auth/oidc/callback
```

将以下占位配置填写到容器环境变量（Podman/Docker `--env-file` 或 Compose `environment`），或应用工作目录下不提交到 Git 的 `.env`。进程环境变量优先，修改后重启。不要把客户端密钥写入前端变量、仓库、日志或截图。

```dotenv
OIDC_ENABLED=true
OIDC_ISSUER=https://identity.example.com
OIDC_CLIENT_ID=panwatch
OIDC_CLIENT_SECRET=replace-with-your-provider-client-secret
OIDC_PUBLIC_URL=https://portfolio.example.com
OIDC_ALLOWED_SUBJECTS=["exact-owner-subject-from-provider"]
```

Issuer 必须与 discovery 中的值完全相同，包括租户路径和末尾斜杠。通过提供方管理界面或其正式文档中的 claim 查看方式，取得所有者稳定的 `sub`。允许名单是 JSON 字符串数组，按当前 issuer 下的 sub 精确匹配；不要填显示名或假定邮箱就是 sub。空名单或无效配置会拒绝启动/访问，不会退回免登录。

服务端必须能用可信 HTTPS 访问 discovery、token endpoint 与 JWKS。默认只信任与 issuer 同源的端点。提供方若使用多个域名，需要显式增加它所控制、文档确认的来源：

```dotenv
OIDC_ALLOWED_ENDPOINT_ORIGINS=["https://tokens.identity.example.com","https://keys.identity.example.com"]
```

此名单限制服务端请求与登录跳转。包含用户名密码、片段、查询参数、模糊路径的端点会被拒绝，也不会跟随 HTTP 重定向。可以配置可信的内网身份提供方，但配置本身属于管理员信任边界。请给私有提供方设置 DNS 与可信证书，不要关闭证书校验。

## 选项与密码兼容

- `OIDC_TOKEN_AUTH_METHOD` 默认 `client_secret_basic`，也支持 `client_secret_post` 或公共客户端的 `none`（仍使用 PKCE）
- `OIDC_ALLOW_PASSWORD_LOGIN` 默认 `false`。显式设为 `true` 才保留已配置的本地密码作为备用入口
- `OIDC_SESSION_HOURS` 默认 12，范围为 1–168 的整数，是本地会话的绝对有效期
- `OIDC_ALLOWED_ENDPOINT_ORIGINS` 默认 `[]`
- `OIDC_ALLOW_HTTP_LOCALHOST` 默认 `false`，仅开发测试时允许字面 `localhost`、`127.0.0.1`、`::1` 的 HTTP

纯 SSO 模式在后端禁用密码登录、密码修改及旧密码 JWT，不只是隐藏按钮。只要启用 OIDC，就禁止公开首次设置账户，即使允许密码备用登录也一样。需要备用密码时，请在启用前配置；原有密码哈希不会被删除或改写。关闭 OIDC 会恢复旧密码策略，包括未设置密码时的初始行为，因此公网实例关闭前务必确认已配置本地密码。

## Caddy、Cloudflare Tunnel、内网与子路径

`OIDC_PUBLIC_URL` 指定唯一浏览器端 HTTPS 地址，不从 Host、转发头或回调参数推断。Caddy/Cloudflare 在外层终止 TLS 后，通过内网 HTTP 连接 PanWatch 是可以的，不需要关闭 Secure Cookie。不要直接公开后端端口，也不要信任任意客户端的转发头。

内网与隧道登录请使用同一个 HTTPS 域名，可让内网 DNS 将该域名解析到本地 Caddy。HTTP 的内网 IP 不是另一个认证来源，Secure Cookie 不会在那里工作。

使用子路径时，还需安装并配置独立的反向代理子路径功能。例如 `PANWATCH_BASE_PATH=/apps/panwatch`，`OIDC_PUBLIC_URL=https://portfolio.example.com/apps/panwatch`，回调登记为 `https://portfolio.example.com/apps/panwatch/api/auth/oidc/callback`。两者路径必须一致，Cookie 路径为 `/apps/panwatch/`。仅此 OIDC 分支不提供前端或代理子路径路由。

不要缓存 `/api/auth/*`，包括重定向和失败响应。应用已设置 `Cache-Control: no-store`。Caddy、隧道和应用 access log 应隐藏回调的 query，因为标准回调含短效授权码与 state。不要开启 token exchange 的 HTTP body/debug 日志；应用自身不记录提供方响应、claim、授权码、密钥或 token。

## 会话、安全与退出

会话 Cookie 使用 Secure、HttpOnly、host-only、SameSite=Lax。前端先访问 `/api/auth/status` 验证登录，CSRF token 只保存在内存。Cookie 认证的 POST/PUT/PATCH/DELETE 必须同时提供 `X-CSRF-Token` 与精确匹配的 Origin，POST SSE 也执行相同检查；已经打开的 SSE 在每次输出前重新检查原会话，退出或过期后不再发送私有数据。启用 OIDC 时 CORS 只允许配置的应用来源。不要在同源下托管不可信应用。

服务端交换并验证 ID Token 后丢弃提供方 access/refresh/ID token，不返回给前端，也不持久化。检查包括 JWKS 签名、明确的非对称算法名单、issuer、audience、expiry、iat、nonce 与 azp。登录交易十分钟有效、一次性使用，并将 state/PKCE 绑定到发起浏览器。

本地 Cookie 是随机不透明会话 ID；数据库仅保存其哈希、CSRF token、所有者身份、配置绑定和绝对过期时间。重启后仍有效；过期、issuer/client/public URL 变化或移除允许的 sub 后拒绝访问。登录时清理过期行，新登录成功后撤销该浏览器的旧会话。开始或取消重试时，保留浏览器已携带有效凭据的当前会话，同时废止更早的待完成或尚未送达的登录。

每次登录有服务端单调递增的代际编号、随机浏览器绑定和独立的会话 Cookie 名称。认证必须同时携带同一代际的有效绑定和会话 Cookie；绑定丢失、过期或损坏时需重新登录。回调不覆盖绑定 Cookie 或其他代际的会话 Cookie。提供方验证结束后的数据库写入会原子检查该代际是否已撤销；延迟成功响应不能覆盖新凭据，也不能在新登录退出后恢复旧会话。首次并发登录的绑定各自保留，按服务端编号判断先后，不依赖响应或 Cookie 头顺序。

退出只撤销该请求实际携带有效绑定或会话凭据的代际，不发送 Cookie 删除头。被替换会话的原 CSRF token 和凭据哈希保留至原会话到期，且只能作为退出证明：这样可取消已经提交但 Cookie 尚未到达的重试，同时较早发出的退出请求不能影响它从未观察到的后续登录。这种证明不能用于状态查询、API 或 SSE 认证。其他浏览器和设备不受影响。显式启用密码备用时，已配置且验证有效的密码 Bearer token 加上规范 Origin，也可取消该请求实际携带绑定的代际，包括待完成或 Cookie 尚未送达的首次 OIDC 登录。匿名请求或 SSO-only 下的密码 JWT 不具备该权限。

登录事务仍为十分钟、一次性使用；不具备认证能力的绑定 Cookie 从登录开始保留“会话有效期加十分钟”，让延迟成功响应仍能被后续退出识别。代际记录和退出证明都有有界过期时间；终止且从未签发会话的尝试最多额外保留十分钟。后续登录或成功响应只清理实际观察到、已经废止且名称永不复用的 Cookie，每个响应最多删除 16 个；延迟清理不会删除更新的凭据。正常登录保留一对绑定/会话 Cookie，重试期间增加一个绑定。大量首次响应延迟到达时，可能暂时保留额外的无效 Cookie；分批查询和逐步清理让状态、退出和重新登录继续恢复，不丢弃有效凭据。全局最多 128 个待完成事务的上限不变。

从固定名称 OIDC Cookie 升级后，各路径都需要重新登录一次。旧 Cookie 和旧会话缺少代际记录，不能用于认证，并按原期限自然过期。数据库初始化仅新增代际表，不修改已有表结构。

退出是 CSRF 保护的 POST，会撤销本地会话，不会退出提供方或其他应用。提供方可能在下次登录时直接放行，共用电脑上请使用提供方自己的退出入口。提供方侧停用/退出不会即时撤销已存在的 PanWatch 会话；删除允许的 sub 并重启应用即可使该身份失效。不支持 back-channel logout 或刷新 token。

MCP 仍使用现有带 scope 的 `pwmcp_` PAT，按自己的过期和撤销规则工作；OIDC 不是 MCP OAuth Server。普通 REST 客户端可使用同源 Cookie/status/CSRF 流程，或在显式允许密码备用时继续使用原密码 bearer-token 流程。提供方 ID/access token 不能直接作为 PanWatch bearer token。

## 验证边界

自动测试使用一次性签名的模拟提供方，覆盖授权码/PKCE 正常路径，以及签名、issuer、audience、nonce、expiry、state、浏览器绑定、重放、允许名单、无效配置、会话过期/轮换、密码兼容、CSRF/SSE 和退出撤销。前端测试覆盖先验证后放行、错误重试、旧 JWT 和确认退出。尚未连接真实提供方，也未验证用户 Orange Pi/Caddy/Cloudflare 实际部署；启用生产 SSO 前需要另行验证。

协议参考：[OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html)、[OAuth Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html)、[PyJWT API](https://pyjwt.readthedocs.io/en/stable/api.html)。

公网部署应在可信的 Caddy/Cloudflare 边缘层限制 `/api/auth/oidc/login` 的请求频率。应用最多保留 128 个待完成交易，十分钟过期；匿名客户端可能耗尽此额度，使正常登录暂时返回 429，持续攻击可能延长中断。这个存储上限不能代替防滥用措施；本功能不会自动修改边缘层或部署设置。


## GET 只读与接口迁移

Cookie CSRF 防护要求 GET/HEAD/OPTIONS 不触发业务操作。旧版调用方请按以下方式迁移：

- 执行自检改用 `POST /api/health/selfcheck`，原有 `notify_send` 和逗号分隔的 `keys` 查询参数不变。只有 `GET /api/health/selfcheck?list=1` 返回只读清单；其他 GET 返回 405 和 `Allow: POST`。即使不发通知，AI 探测仍可能消耗配额。前端全部和逐项自检均已改为 POST。
- 刷新入场候选使用已有 `POST /api/recommendations/entry-candidates/refresh`。旧 `GET /api/recommendations/entry-candidates?refresh=true` 返回明确的迁移错误，不改变数据；普通列表 GET 保持可用。前端 SDK 的 `listEntryCandidates` 已移除 `refresh` 选项，需先调用 `refreshEntryCandidates` 再读列表。
- `GET /api/assistant/exports/{export_id}` 只读取任务状态和结果，不启动排队中的 AI 导出。创建、重试 POST 和服务启动恢复负责执行任务；轮询客户端无需修改。

以上 OIDC Cookie POST 都需要状态接口提供的 CSRF 请求头及规范 Origin。显式启用的密码 Bearer 客户端也使用 POST，无需 Cookie CSRF。

会话和登录事务 Cookie 名称按可信配置中的规范部署路径隔离，各路径均在自己的名称空间内使用独立代际后缀。因此从 `/` 迁移到 `/apps/panwatch` 时，残留的祖先路径 Cookie 不会覆盖新 Cookie。从固定名称 Cookie 升级后，各路径均需重新登录一次；旧 Cookie 会被忽略并自然过期。

若另一标签页登录并改变当前会话，退出收到 `csrf_invalid` 后会重新读取不缓存的认证状态，最多用新的 CSRF 重试一次。网络失败、其他错误以及其他业务写操作不会自动重放；退出失败时保留已验证会话，便于手动重试。
