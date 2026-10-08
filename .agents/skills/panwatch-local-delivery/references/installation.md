# 项目级安装与调用

维护唯一技能源 `.agents/skills/panwatch-local-delivery/`。安装脚本从自身位置定位项目，与终端 cwd 无关；不修改 HOME、全局技能、agent 配置或模型权限。

## 安装

项目根执行（Python 标准库，无需下载依赖或调用 agent CLI）：

```sh
python3 scripts/install-agent-skills.py
python3 scripts/install-agent-skills.py --check
```

默认检查 Codex、Claude Code、Cursor、Gemini CLI。Codex/Cursor/Gemini 使用已有共享目录；Claude Code 创建 `.claude/skills/panwatch-local-delivery` 的相对 symlink。修改技能源后链接即时指向新内容，避免多份文档漂移。

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
