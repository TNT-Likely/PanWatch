import { describe, expect, it } from 'vitest'
// @ts-expect-error The repository check is a JavaScript build tool.
import { findUiViolations } from '../../scripts/check-ui-primitives.mjs'

describe('UI primitive enforcement', () => {
  it('rejects native controls, browser dialogs and unstyled scroll areas', () => {
    for (const source of [
      'const x = <select><option value="1">One</option></select>',
      'const x = <dialog />', 'window.confirm("Delete?")', 'globalThis.alert("Failed")',
      'prompt("Name")', 'const x = <div className="overflow-y-auto" />',
      'const x = <div className={`max-h-80 ${size} overflow-auto`} />',
    ]) expect(findUiViolations(source).length).toBeGreaterThan(0)
  })
  it('accepts shared components and ignores comments containing forbidden examples', () => {
    expect(findUiViolations(`
      // window.confirm("Delete?") and <select> are forbidden.
      const ask = useConfirm()
      await ask(message)
      const x = <div className="overflow-auto scrollbar"><Select><SelectItem value="all" /></Select></div>
      const y = <div className="overflow-x-auto scrollbar-none" />
    `)).toEqual([])
  })
})
