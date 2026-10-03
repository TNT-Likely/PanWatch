# Shared UI conventions

[简体中文](UI_GUIDELINES.zh-CN.md)

Feature code must reuse the primitives in `packages/base-ui/src/components/ui`. Read this guide before adding or changing interactive UI.

## Scroll regions

Add `scrollbar` to every native element using `overflow-auto`, `overflow-x-auto`, `overflow-y-auto`, or a scroll variant. It provides a transparent track and a themed thin thumb in light and dark mode. Use `scrollbar-none` only for intentional horizontal chip navigation; do not hide a long list's only scrolling affordance. Scroll classes on shared components such as `DialogContent` inherit their primitive's style.

Constrain nested flex scroll regions with `min-h-0`, bound their height to the viewport, and use `overscroll-contain` inside floating panels. Keep table column headers outside the scrolling row group, or provide an opaque sticky header with proper stacking. Verify header/cell alignment after scrolling, long content, and no horizontal clipping at a 390 px viewport. Test both light and dark themes; a light scrollbar track in a dark panel is a defect.

## Selects

Use `Select`, `SelectTrigger`, `SelectValue`, `SelectContent`, and `SelectItem` from the shared Select module. Give triggers an accessible label and use `onValueChange`. Radix reserves an empty item value: use a nonempty UI sentinel for “all/default” and translate it to the empty API value explicitly. Do not introduce native `<select>` or `<option>`, or hand-build a menu when a shared primitive provides that interaction. Preserve keyboard navigation and focus behavior.

## Confirmations and feedback

Use `useConfirm` from the shared `confirm-dialog` module, under the application's localized `ConfirmProvider`:

```tsx
const confirmAction = useConfirm()
if (!(await confirmAction(message, { destructive: true }))) return
await removeItem()
```

Keep the mutation after the awaited acceptance. Cancellation, Escape, dismissal, and provider unmount resolve as false. The shared dialog focuses Cancel first, restores the opener's focus, and queues concurrent requests. The app supplies translated labels; callers supply translated descriptions. For failure/success feedback, use `useToast`; never use browser `confirm`, `alert`, or `prompt`. Reuse shared `Dialog` for form dialogs and `Popover` for contextual panels.

## Verification and enforcement

Run `pnpm check:ui`, `pnpm check:i18n`, `pnpm check:market-colors`, relevant Vitest tests, and `pnpm build` before opening a PR. `check:ui` parses application and shared UI TypeScript and rejects native selects/dialogs, browser dialog calls, and unstyled native scroll regions. It is included in the existing PR translation check and frontend build, without a legacy exemption list. It checks static JSX class declarations and direct browser API access; visual and keyboard QA are still required for dynamic styles and nested overlays.

For floating panels and new controls, verify light/dark, desktop/mobile, open/close, keyboard focus, Escape, and scrolled content. Do not claim visual validation unless it was actually performed.
