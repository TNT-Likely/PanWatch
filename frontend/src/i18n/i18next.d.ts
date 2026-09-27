import 'i18next'

import type { resources } from './resources'

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'common'
    returnNull: false
    resources: (typeof resources)['zh-CN']
  }
}
