# Frontend Workspace Packages

- `@panwatch/api`: 统一的 HTTP 请求入口与领域 API（认证、版本、股票等）。
- `@panwatch/base-ui`: 基础 UI 组件与样式工具（原 `src/components/ui/*` 已迁移）。
- `@panwatch/biz-ui`: 业务组件与业务复用逻辑（原 `src/components/*` 业务组件已迁移）。

当前前端页面已统一从 `@panwatch/api` 发起接口请求，避免在页面中直接调用 `fetch`。
