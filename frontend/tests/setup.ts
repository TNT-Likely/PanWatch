import { afterEach, beforeEach } from 'vitest'
import i18n from '../src/i18n'

// jsdom has no native top-layer state. NWSAPI 2.2.27 recursively calls
// Element.matches for these selectors when Floating UI positions a popover.
// Keep the real Radix components and focus behavior in tests.
const originalMatches = Element.prototype.matches
Element.prototype.matches = function (selector: string) {
  if ([':modal', ':fullscreen', ':popover-open'].includes(selector)) return false
  return originalMatches.call(this, selector)
}

beforeEach(async () => {
  window.localStorage.removeItem('panwatch-locale')
  await i18n.changeLanguage('zh-CN')
})

afterEach(() => {
  window.localStorage.removeItem('panwatch-locale')
  void i18n.changeLanguage('zh-CN')
})
