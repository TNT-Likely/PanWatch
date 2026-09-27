# Frontend internationalization

PanWatch defaults to `zh-CN`. A locale changes only interface language; it must not
implicitly change currency, market, or timezone.

When adding user-facing copy:

1. Put the message in the closest namespace in `resources.ts` and use a semantic key.
2. Add both `zh-CN` and `en-US` text for migrated surfaces.
3. Use `useTranslation` in React components and the helpers in `format.ts` for
   locale-sensitive values.
4. Keep server error text as diagnostic data. New UI behavior must not branch on a
   translated error message.

The English locale is experimental. Unmigrated business pages may remain in Chinese;
Chinese is the runtime fallback for missing translation resources.
