# Frontend internationalization

PanWatch chooses `zh-CN` for new visitors whose browser language is Chinese and
`en-US` for everyone else. A saved preference always wins. A locale changes only
interface language; it must not implicitly change currency, market, or timezone.

When adding user-facing copy:

1. Put the message in the closest file under `locales/<locale>/` and use a semantic key.
2. Add both `zh-CN` and `en-US` text for migrated surfaces.
3. Use `useTranslation` in React components and the helpers in `format.ts` for
   locale-sensitive values.
4. Keep server error text as diagnostic data. New UI behavior must not branch on a
   translated error message.

Chinese is the runtime fallback for missing translation resources.

Run `pnpm check:i18n` to ensure surfaces already declared migrated do not regress by
adding Chinese UI literals. The checker intentionally ignores comments and console
diagnostics; expand its `migratedFiles` list whenever a new page completes migration.
