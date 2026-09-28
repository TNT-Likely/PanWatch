import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sourceRoots = [
  path.join(frontendRoot, 'src'),
  path.join(frontendRoot, 'packages', 'biz-ui', 'src'),
]

const collectSourceFiles = (directory) => fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
  const absolutePath = path.join(directory, entry.name)
  if (entry.isDirectory()) return collectSourceFiles(absolutePath)
  if (!/\.(ts|tsx)$/.test(entry.name) || /\.(test|spec)\.(ts|tsx)$/.test(entry.name)) return []
  const relativePath = path.relative(frontendRoot, absolutePath)
  return relativePath.includes('/i18n/locales/') ? [] : [relativePath]
})

// These files define or preview the two concrete palettes, so explicit red and
// green tokens are intentional there. All feature code should consume semantic
// market-up / market-down tokens or the runtime palette instead.
const explicitPaletteFiles = new Set([
  'src/lib/market-colors.ts',
  'src/pages/Settings.tsx',
])

const hardcodedMarketColor = /(?:\b(?:text|bg|border)-(?:rose|red|emerald|green)-\d{2,3}(?:\/\d+)?\b|#(?:e11d48|ef4444|dc2626|059669|10b981|16a34a|e53935|43a047)\b)/i
const marketMeaning = /(?:\b(?:bullish|bearish|buy|sell|reduce|delta|pnl|up|down)\b|\badd\s*:|change(?:_pct|Pct)?|return_pct|portfolio_return|benchmark_return|excess_return|relative_drawdown|avg_return|contribution|net_?buy)/i
const legacyToken = /\b(?:text|bg|border)-stock-(?:up|down)\b/

const failures = []

for (const relativePath of sourceRoots.flatMap(collectSourceFiles).sort()) {
  const sourceText = fs.readFileSync(path.join(frontendRoot, relativePath), 'utf8')
  const lines = sourceText.split(/\r?\n/)

  lines.forEach((line, index) => {
    if (legacyToken.test(line)) {
      failures.push(`${relativePath}:${index + 1} legacy stock color token`)
    }

    if (explicitPaletteFiles.has(relativePath) || line.includes('market-color-fixed') || !hardcodedMarketColor.test(line)) return

    // Include a small amount of surrounding context so multi-line conditionals
    // such as `if (action === 'buy') { return 'text-red-...' }` are caught.
    const context = lines.slice(Math.max(0, index - 2), Math.min(lines.length, index + 2)).join(' ')
    if (marketMeaning.test(context)) {
      failures.push(`${relativePath}:${index + 1} hardcoded directional color: ${line.trim()}`)
    }
  })
}

if (failures.length > 0) {
  console.error('Market-sensitive UI code must use semantic market colors:')
  for (const failure of failures) console.error(`- ${failure}`)
  process.exitCode = 1
} else {
  console.log('Market color check passed')
}
