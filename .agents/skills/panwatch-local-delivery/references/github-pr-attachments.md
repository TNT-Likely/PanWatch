# GitHub PR 测试报告附件

完整交付时按用户已约定的自动 PR 与测试报告附件流程执行。参考 [BeeCount-Cloud PR #119](https://github.com/TNT-Likely/BeeCount-Cloud/pull/119) 的独立 ZIP 交付方式；本说明可独立执行，不依赖 BeeCount 工作区。

## 上传和发布

1. 核对目标仓库、PR、分支、测试对应的源码 SHA／内容 hash、构建 hash 和 run ID。按 [报告交付](delivery.md) 完成脱敏、解压与清单校验，记录 ZIP 文件名、大小和 SHA256。固定待上传包；上传后不重新生成同名包替换其内容。
2. 核对当前工具能力及 [GitHub 官方附件说明](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files) 的类型与大小限制，不硬编码上限。CLI/API 不支持 ZIP 时检查网页文件选择入口，不据此改为用户手动上传。
3. 阅读当前浏览器工具说明，使用支持文件选择的已可用浏览器打开目标 PR。核对实际登录账号和写权限，复用已有登录。需要登录时由用户完成登录，登录后继续已有流程，无需重新询问附件授权。
4. 使用 PR 正文或评论编辑器的「Attach files / Add files / 附加文件」控件，通过工具提供的文件选择器上传本地 ZIP。工具支持文件选择事件时先监听，再点击实际上传控件，设置已核验的 ZIP 路径。不读取 cookie、会话或调用内部上传接口代替网页操作。
5. 等待上传提示变成真实的 GitHub 附件 Markdown 链接。记录链接，无需另发评论；若使用评论编辑器准备附件，正文发布后仅清空本次临时上传草稿，保留用户已有草稿。
6. 重新读取最新 PR 正文，保留已有说明和用户编辑，在 `Validation` 内增加或更新 `Test report` 小节，包含 ZIP 下载链接、解压打开 `index.html` 的说明、测试对应的源码／产物身份、ZIP SHA256，以及失败和未执行范围。已有同一包的附件时复用并核验，避免重复上传或评论。发布后检查实际渲染的正文，确认链接显示正确。
7. 从已发布链接回下载至新的私有临时目录，对比文件大小和 SHA256，检查 ZIP 可读取。记录 PR URL、附件 URL、文件名、对应源码／构建身份、上传与下载校验结果和交付截图。回执留在包外，避免改变已上传 ZIP 的 hash。

正文示例（替换所有示意值后再发布）：

```markdown
### Test report

[Download the test report (ZIP)](<verified attachment URL>)

Extract the archive and open `index.html` for scenario results, screenshots,
data comparisons, retained failures and untested paths.

Tested source: `<commit SHA / content hash>`; frontend build: `<build hash>`.
ZIP SHA256: `<verified SHA256>`.
```

取得附件 URL 只证明上传阶段完成。以正文已发布、链接可访问、回下载文件大小与 SHA256 一致且 ZIP 可读取作为附件交付完成的证据。

## 失败边界

- 网站拒绝、浏览器能力缺失、登录或权限受阻时，记录具体失败阶段和原因；其他可执行的 PR 步骤继续完成，正文注明 `Report attachment pending`，交付同一完整本地包。安全或权限拦截时停止该操作，不换工具绕过拦截。
- 下载失败先确认链接已写入并发布，有限重试实际失败的环节；避免重新上传同一个包。已上传但无法回下载时分别记录「已上传」和「下载校验未完成」，不能声称全流程通过。
- 用户明确要求仅本地或不上传时遵循限制，提供本地包及打开说明；不将本机绝对路径伪装为公共链接。
- 不为存放报告新建 Release、仓库或其他托管，不扩大为合并、部署或发版。

## 关联 Issue

核对 Issue 的实际需求，再在 PR 正文链接它。仅修复其中一部分时注明本次覆盖范围；完整解决时才使用自动关闭关键词。
