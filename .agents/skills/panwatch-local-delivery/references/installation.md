# 项目级安装与调用

维护唯一技能源 `.agents/skills/panwatch-local-delivery/`。安装脚本从自身位置定位项目，与终端 cwd 无关；不修改 HOME、全局技能、agent 配置或模型权限。

## 安装

项目根执行（Python 标准库，无需下载依赖或调用 agent CLI）：

```sh
python3 scripts/install-agent-skills.py
python3 scripts/install-agent-skills.py --check
```

默认检查 Codex、Claude Code、Cursor、Gemini CLI。Codex/Cursor/Gemini 使用已有共享目录；Claude Code 创建 `.claude/skills/panwatch-local-delivery` 的相对 symlink。修改技能源后链接即时指向新内容，避免多份文档漂移。

### Claude Code 文件硬链接

需要真实目录入口、同时复用 `.agents/skills/` 中的文件时，使用逐文件硬链接（目录本身不能硬链接）：

```sh
python3 scripts/install-agent-skills.py --agent claude --mode hardlink
python3 scripts/install-agent-skills.py --agent claude --check
```

生成 `.claude/skills/<name>/SKILL.md` 及同目录相对引用资源；每个文件与共享源具有相同的设备号和 inode，不复制技能正文。已有工具安装的 symlink/copy 入口需先执行 `--agent claude --uninstall`，再安装 `--mode hardlink`；不会自动覆盖已有入口。

只维护 `.agents/skills/` 下的源文件。硬链接两端原地写入会同步改变同一文件；编辑器原子保存、Git checkout 等替换 inode 的操作，以及新增／删除资源后，重新运行 `--mode hardlink` 刷新，再执行 `--check`。检查会核对全部文件的 inode，不仅比较内容；独立替换且被修改的安装文件会阻止刷新或卸载。

可在项目根核验技能入口（Python 标准库，不调用模型）：

```sh
python3 -c 'from pathlib import Path; s = Path(".agents/skills/panwatch-local-delivery/SKILL.md"); t = Path(".claude/skills/panwatch-local-delivery/SKILL.md"); assert not t.parent.is_symlink() and not t.is_symlink() and s.samefile(t); print("same inode:", s.stat().st_ino, t.stat().st_ino, "links:", s.stat().st_nlink)'
```

硬链接要求源与入口处于同一文件系统；不支持时明确选择 `--mode link` 或 `--mode copy`，安装器不会静默降级。

按需选择：

```sh
python3 scripts/install-agent-skills.py --agent claude --dry-run
python3 scripts/install-agent-skills.py --agent codex --agent claude
python3 scripts/install-agent-skills.py --agent claude --mode copy
python3 scripts/install-agent-skills.py --agent claude --uninstall
```

文件系统不能建立 symlink 时显式选 copy。复制版带工具所有权标记和内容 hash；再次安装只更新未被本地修改的工具自有副本。冲突时拒绝覆盖，无强制覆盖选项。卸载只删除工具拥有且未被修改的 Claude 入口，不删除共享技能源、其他技能或用户配置。

`.claude/` 已被仓库忽略，生成入口不提交；团队成员拉取共享技能源后运行安装脚本即可。安装和 --check 验证文件布局/内容，不等同于各 agent 的运行时加载验收。

## 发现规则与调用

官方规则核对日期：2026-10-08。客户端版本可能影响加载；以实际技能列表确认。

| Agent | 项目级入口 | 显式调用与刷新 | 官方来源 |
|---|---|---|---|
| Codex | .agents/skills/ | `$panwatch-local-delivery`；新增后确认技能选择器，必要时重开会话 | [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills) |
| Claude Code | .claude/skills/；支持技能文件夹 symlink | `/panwatch-local-delivery`；新建根技能目录时 `/reload-skills`，检查 `/skills` | [Claude Code skills](https://code.claude.com/docs/en/skills) |
| Cursor | .agents/skills/ | Agent 输入 `/` 查看技能，选中对应项 | [Cursor skills](https://cursor.com/docs/skills) |
| Gemini CLI | .agents/skills/ 的 workspace alias | `/skills reload`、`/skills list`；按自然语言请求激活 | [Gemini CLI skills](https://geminicli.com/docs/cli/using-agent-skills/) |

技能保留通用 name/description 和相对引用，agents/openai.yaml 仅提供 Codex 界面元数据，不作为其他 agent 的硬依赖。Gemini 的 workspace trust 和技能启用、Claude 的项目技能加载策略等仍由客户端控制；安装脚本不修改这些设置。

对于未列出的 agent，先核对其官方技能发现规则，再扩展映射并测试项目边界；不要默认向 ~/.xxx/skills 写入或把技能正文塞进全局规则。
