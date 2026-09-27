import { afterEach, beforeEach } from 'vitest'
import i18n from '../src/i18n'

beforeEach(async () => {
  window.localStorage.removeItem('panwatch-locale')
  window.localStorage.removeItem('panwatch-ai-report-language')
  await i18n.changeLanguage('zh-CN')
})

afterEach(() => {
  window.localStorage.removeItem('panwatch-locale')
  window.localStorage.removeItem('panwatch-ai-report-language')
  void i18n.changeLanguage('zh-CN')
})
