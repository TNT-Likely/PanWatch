# PanWatch Release Notes / 发版说明

[English](#english) · [简体中文](#简体中文)

Release notes are maintained in English. They describe user-visible value,
upgrade steps, compatibility, and known limitations instead of being generated
from commit messages.

为降低长期维护成本并避免中英文内容漂移，后续发版说明统一使用英文。界面、README 和用户文档
仍继续维护中英文版本。

## Published notes / 已发布说明

- [0.15.0](0.15.0.md)

## English

### Preparing a release

1. Copy [`TEMPLATE.md`](TEMPLATE.md) to `docs/releases/<version>.md`.
2. Replace every placeholder and complete every section in English.
3. Describe user-visible behavior. Keep internal refactors out unless they change reliability, compatibility, performance, or extension boundaries.
4. State data migrations, configuration changes, deprecations, and known limitations explicitly. Write `None` when there are none.
5. Commit the note in the release PR before creating the tag.
6. Run the validator locally:

   ```bash
   python scripts/prepare_release_notes.py \
     --version 0.16.0 \
     --repository TNT-Likely/PanWatch \
     --source docs/releases/0.16.0.md \
     --output /tmp/panwatch-release-notes.md
   ```

The tag workflow rejects a release when its versioned note is missing, incomplete,
out of order, or still contains placeholders. It appends Docker image and compare
links before publishing the GitHub Release.

## 简体中文

### 准备新版本

1. 将 [`TEMPLATE.md`](TEMPLATE.md) 复制为 `docs/releases/<version>.md`。
2. 用英文替换全部占位内容。
3. 重点描述用户可见变化；内部重构只有影响可靠性、兼容性、性能或扩展边界时才需要写入。
4. 明确说明数据迁移、配置变化、废弃项和已知限制；没有时写 `None`。
5. 在创建 tag 前，将版本说明随发版 PR 一起提交。
6. 使用上面的命令在本地运行校验器。

Tag 流水线会拒绝缺少英文版本说明、章节不完整、顺序错误或仍含占位内容的发版。
GitHub Release 发布前会自动追加 Docker 镜像和版本对比链接。
