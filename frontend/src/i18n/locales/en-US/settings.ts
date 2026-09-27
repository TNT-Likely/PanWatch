import type { TranslationShape } from '../../resource-types'
import { settings as zhSettings } from '../zh-CN/settings'

export const settings = {
  account: {
    menuTitle: 'Account and settings',
    avatarAlt: 'Avatar',
    themeTitle: 'Theme',
    theme: { light: 'Light', dark: 'Dark', system: 'System' },
    selfCheck: 'System check',
    signOut: 'Sign out',
  },
  language: {
    title: 'Interface language',
    simplifiedChinese: '简体中文',
    english: 'English',
    switchAria: 'Switch interface language',
    quickSwitch: '简体中文',
  },
} as const satisfies TranslationShape<typeof zhSettings>
