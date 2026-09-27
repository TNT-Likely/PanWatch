import { auth as enAuth } from './locales/en-US/auth'
import { common as enCommon } from './locales/en-US/common'
import { navigation as enNavigation } from './locales/en-US/navigation'
import { settings as enSettings } from './locales/en-US/settings'
import { auth as zhAuth } from './locales/zh-CN/auth'
import { common as zhCommon } from './locales/zh-CN/common'
import { navigation as zhNavigation } from './locales/zh-CN/navigation'
import { settings as zhSettings } from './locales/zh-CN/settings'
import type { TranslationShape } from './resource-types'

export const zhCN = {
  common: zhCommon,
  auth: zhAuth,
  navigation: zhNavigation,
  settings: zhSettings,
} as const

export const enUS = {
  common: enCommon,
  auth: enAuth,
  navigation: enNavigation,
  settings: enSettings,
} as const satisfies TranslationShape<typeof zhCN>

export const resources = {
  'zh-CN': zhCN,
  'en-US': enUS,
} as const

export type NavigationItemKey = keyof typeof zhCN.navigation.items
