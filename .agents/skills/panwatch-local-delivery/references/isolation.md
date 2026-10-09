# PanWatch 隔离与运行

真实验收前读取。以下映射来自当前源码，配置或写入路径变化时重新核对，并更新工具检查后再运行。

## 项目入口与保护资源

React/Vite 前端与 Python/FastAPI 后端；正常入口 `server:app`。先记录现用服务的 PID、启动时间、监听端口与数据目录，列为保护资源，不借用或重启。

| 位置 | 当前行为 | QA 适配 |
|---|---|---|
| `src/platform/persistence/database.py` | DB 固定在源码根 `data/panwatch.db`，导入即创建目录/engine | 独立源码副本；仅设 DATA_DIR 无法隔离 |
| `src/platform/marketdata/stock_list.py` | 股票缓存固定在源码根 `data/` | 随源码副本隔离 |
| `src/platform/runtime/config.py` | 从 cwd 的 `.env` 加载 | cwd 固定为 QA 源码；生成空 QA .env |
| 事件状态、头像 | 部分写入读取 DATA_DIR | 显式设为 QA 源码的 data/ |
| `server.py` | 合并证书写源码根 data/，浏览器可能写共享缓存 | 检查缓存位置，禁止启动时自动安装浏览器 |
| 静态路由 | 服务源码根 `static/` | QA 副本构建前端并记录产物 hash |
| `frontend/vite.config.ts` | dev 默认 5183，代理到现用 8000 | 使用后端同源静态产物 |
| 本地 package editable 安装 | .venv 可能导入原 checkout | 置顶副本 package 路径，实际核对模块来源 |
| 正常生命周期 | 初始化种子、日志与调度器 | 新 DB、新鉴权；核对默认后台行为及外部副作用 |

按本次代码搜索持久化、缓存、配置和外部客户端，维护全部写入及副作用清单。辅助工具验证已知映射和目录包含关系；不替代新代码审查，也不是操作系统级沙箱。

## 准备

工具仅使用 Python 标准库。项目根执行：

```sh
python3 .agents/skills/panwatch-local-delivery/scripts/isolated_qa.py prepare --repo . --python .venv/bin/python
```

输出本次私有 run 路径。导出 Git 管理的当前内容和未忽略的新源码，保留未提交修改及删除结果；排除 .env*、现用数据、依赖、缓存、报告、AGENTS.md 和安装生成目录。拒绝源码 symlink 和项目内部的 run 目录。

```sh
qa_run_dir='<本次输出的 run 路径>'
python3 .agents/skills/panwatch-local-delivery/scripts/isolated_qa.py preflight --run "$qa_run_dir" --check-source
```

记录基准 SHA、内容 hash、技能版本/hash、解释器、已知写入路径与 loopback origin。run 权限 0700；新凭证仅存在 `private/credentials.json`，不打印或打包。创建空 QA .env、独立临时及缓存目录。

preflight 在服务导入前拒绝所有权、权限、路径、symlink、源码完整性、配置映射或产物不匹配。`--check-source` 拒绝原工作区已变化的旧副本。

## 前端与服务

在副本内安装锁定依赖并构建，不共享可写 node_modules：

```sh
pnpm --dir "$qa_run_dir/source/frontend" install --frozen-lockfile
pnpm --dir "$qa_run_dir/source/frontend" build
python3 .agents/skills/panwatch-local-delivery/scripts/isolated_qa.py seal-frontend --run "$qa_run_dir"
python3 .agents/skills/panwatch-local-delivery/scripts/isolated_qa.py serve --run "$qa_run_dir"
```

seal-frontend 将本次 dist 拷贝为 source/static 并记录文件 hash。serve 再检查隔离及前端身份，拒绝占用端口，以正常入口绑定 127.0.0.1，禁用 reload。仅 API 场景显式 `serve --api-only`，不能宣称 UI 已测。

serve 为前台进程。人工交接须确认服务生命周期独立于会结束的工具会话；可用本次 run 私有描述文件交给本机服务管理器托管，记录完整 job 身份、PID、启动命令、日志和停止方法。例如 macOS 使用当前用户 launchd job，不安装到系统登录启动目录。不能仅凭临时 exec 会话就承诺聊天结束后仍可访问。启动后核对 `private/runtime.json` 中 PID、启动身份、模块来源、实际 DB/config、origin，再核对实际监听、鉴权 readiness 与页面。重启等待 TIME_WAIT 不算服务仍在运行；端口检查必须拒绝活跃监听者。该记录不等于业务 PASS。

启动不继承现用 AI/通知密钥、代理、OTel 或 PYTHONPATH；只带系统必需变量、新凭证与 QA 路径。HOME 保留系统身份，新增缓存单独指定。依赖解释器只读复用，本地源码 package 必须来自副本。

通过 QA 设置页面/实际接口配置真实模型及必要数据源，沿用已有授权。通知使用授权 QA 接收端；秘密只留在私有环境。正常启动含公开市场数据后台请求；启动前确认其适合本次场景。固定旧行情和故障响应属于受控回放，单独标注。

fixture 仅在新 DB 准备前提；被测用户动作走真实 UI/API。每次源码变化创建新 run，重新构建并重跑受影响场景，保留前次结果。

## 重启、交接、关闭

最终环境重启复用同一 run、数据、凭证和产物，不重新 fixture。服务启动检查原工作区版本；工作区后来变化时，经确认仍验收固定旧版本可使用 `serve --allow-source-drift`，如实记录版本差异。

交接同一 QA 服务和独立浏览器上下文，登录、展示本次对象，交付 origin 与私有凭证位置。人工验收数据不能被复跑覆盖。

用户明确验收完成或要求关闭后，核对 runtime.json 与当前 PID 的启动时间、命令和监听位置，再仅终止本次进程。PID 复用或身份不符时停止清理并说明；禁止按名称批量杀进程。工具不会自动关闭服务或删除 run。
