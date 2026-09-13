# Agent 协作约定

## 提交信息与 PR 标题

提交信息和 Pull Request 标题使用 Conventional Commits 格式：

```text
<type>(<scope>): <subject>
```

常用 `type`：

- `feat`：新增用户可见能力
- `fix`：修复问题
- `refactor`：重构，不改变外部行为
- `perf`：性能优化
- `test`：测试变更
- `docs`：文档变更
- `chore`：工程和维护性变更
- `build` / `ci`：构建或持续集成变更

约定：

- `scope` 使用受影响的模块，例如 `assistant`、`marketdata`、`frontend`。
- `subject` 使用简洁中文描述，首字不加大写要求，不以句号结尾。
- PR 标题应和本次变更的主要用户价值一致，例如：
  `feat(assistant): 优化 Trace 体验并新增发现机会工具`

## PR 正文

PR 正文至少包含以下部分：

1. `背景`：说明问题和用户影响。
2. `变更内容`：按功能模块说明实现和行为变化。
3. `验证`：列出实际执行的测试、构建或检查命令及结果。
4. `边界与风险`：说明未覆盖范围、兼容性和已知限制。
5. `后续计划`：只记录确实需要后续处理的事项。

不要把本地原型、未提交文件或未经验证的结果写成已经交付的功能。

## 分支与合并

- 除非用户明确要求直接推送 `main`，否则在 `codex/` 前缀分支上开发并通过 Pull Request 合并。
- Pull Request 默认使用 squash merge。
- 创建或更新 PR 前先运行与变更相关的测试，并执行 `git diff --check`。
