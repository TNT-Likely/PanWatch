# 统一 UI 约定

[English](UI_GUIDELINES.md)

新增或调整交互前，先阅读本约定，复用 `packages/base-ui/src/components/ui` 中的组件。

## 滚动区域

原生元素使用 `overflow-auto`、`overflow-x-auto`、`overflow-y-auto` 或 scroll 变体时，必须加 `scrollbar`，获得透明轨道和随亮色、深色主题变化的细滑块。只有横向标签导航等明确场景才使用 `scrollbar-none`，不要隐藏长列表唯一的滚动提示。`DialogContent` 等共享组件会继承基础组件的滚动样式。

嵌套 flex 滚动区域加 `min-h-0`，高度受视口限制，浮层内部使用 `overscroll-contain`。表头放在滚动行区域外，或使用有不透明背景和正确层级的 sticky 表头。检查滚动后的表头列对齐、长内容和 390 px 小屏裁切，同时验证亮色和深色主题；深色面板出现白色滚动轨道属于缺陷。

## 下拉选择

使用共享模块的 `Select`、`SelectTrigger`、`SelectValue`、`SelectContent`、`SelectItem`，触发器提供可访问名称，事件用 `onValueChange`。Radix 不接受空的选项值；“全部 / 默认”使用非空的界面占位值，提交时显式转换为空的 API 值。禁止新增原生 `<select>`、`<option>`，也不要重复手写已有组件能提供的菜单交互。保留键盘操作和焦点行为。

## 确认与反馈

在应用的本地化 `ConfirmProvider` 下使用共享 `confirm-dialog` 中的 `useConfirm`：

```tsx
const confirmAction = useConfirm()
if (!(await confirmAction(message, { destructive: true }))) return
await removeItem()
```

必须等待用户确认后再执行变更。取消、Escape、关闭、Provider 卸载均返回 false。共享弹窗默认聚焦取消按钮，关闭后恢复触发器焦点，并按序处理并发请求。应用提供翻译后的按钮文案，调用方提供翻译后的说明。成功或失败反馈使用 `useToast`，禁止浏览器原生 `confirm`、`alert`、`prompt`。表单复用 `Dialog`，上下文浮层复用 `Popover`。

## 检查与验收

开 PR 前运行 `pnpm check:ui`、`pnpm check:i18n`、`pnpm check:market-colors`、相关 Vitest 测试和 `pnpm build`。`check:ui` 解析应用和共享 UI 的 TypeScript，拦截原生选择框 / 弹窗、浏览器弹窗调用和漏用滚动样式的原生元素；现有 PR 文案检查命令和前端构建都会执行，无历史豁免名单。自动检查覆盖静态 JSX 类声明和直接浏览器 API 引用，动态样式和嵌套浮层仍需视觉及键盘验收。

浮层和新控件必须验收亮色 / 深色、桌面 / 手机、打开 / 关闭、键盘焦点、Escape 和滚动后的内容。没有实际进行的视觉检查，不得写成已通过。
