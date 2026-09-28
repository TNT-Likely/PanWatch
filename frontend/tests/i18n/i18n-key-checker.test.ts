import { spawnSync } from 'node:child_process'
import path from 'node:path'

const frontendRoot = process.cwd()
const checker = path.join(frontendRoot, 'scripts/check-i18n-literals.mjs')

function runChecker(fixture: string) {
  return spawnSync(
    process.execPath,
    [checker, '--source', `tests/fixtures/i18n-key-checker/${fixture}`],
    { cwd: frontendRoot, encoding: 'utf8' },
  )
}

describe('i18n key checker', () => {
  it('accepts static and dynamic keys under a valid scoped translator', () => {
    const result = runChecker('valid.tsx')

    expect(result.status).toBe(0)
    expect(result.stdout).toContain('i18n literal and key checks passed')
  })

  it('rejects missing static keys and dynamic prefixes', () => {
    const result = runChecker('missing.tsx')
    const output = `${result.stdout}\n${result.stderr}`

    expect(result.status).toBe(1)
    expect(output).toContain('configuration:errors.timeout')
    expect(output).toContain('configuration:p4.paperTrading.messages.exitReasons.*')
  })
})
