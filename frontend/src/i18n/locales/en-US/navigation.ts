import type { TranslationShape } from '../../resource-types'
import { navigation as zhNavigation } from '../zh-CN/navigation'

export const navigation = {
  items: {
    home: 'Home',
    portfolio: 'Portfolio',
    opportunities: 'Opportunities',
    paperTrading: 'Paper trading',
    assistant: 'Assistant',
    alerts: 'Alerts',
    agents: 'Agents',
    evaluations: 'Evaluation',
    history: 'History',
    dataSources: 'Data sources',
    settings: 'Settings',
  },
} as const satisfies TranslationShape<typeof zhNavigation>
