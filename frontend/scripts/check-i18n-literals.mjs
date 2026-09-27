import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const collectTsxFiles = (directory) => fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
  const absolutePath = path.join(directory, entry.name)
  if (entry.isDirectory()) return collectTsxFiles(absolutePath)
  return entry.name.endsWith('.tsx') ? [path.relative(frontendRoot, absolutePath)] : []
})

// App-owned TSX is checked exhaustively. Shared biz-ui components are added as
// they migrate so legacy screens do not weaken coverage for the main app.
const migratedFiles = [
  ...collectTsxFiles(path.join(frontendRoot, 'src')),
  'packages/biz-ui/src/components/InteractiveKline.tsx',
  'packages/biz-ui/src/components/KlineModal.tsx',
  'packages/biz-ui/src/components/add-position-calculator.tsx',
  'packages/biz-ui/src/components/deep-analysis-modal.tsx',
  'packages/biz-ui/src/components/kline-indicators.tsx',
  'packages/biz-ui/src/components/kline-summary-dialog.tsx',
  'packages/biz-ui/src/components/logs-modal.tsx',
  'packages/biz-ui/src/components/onboarding.tsx',
  'packages/biz-ui/src/components/price-alert-form-dialog.tsx',
  'packages/biz-ui/src/components/stock-insight-modal.tsx',
  'packages/biz-ui/src/components/stock-price-alert-panel.tsx',
  'packages/biz-ui/src/components/suggestion-badge.tsx',
]
const hanPattern = /[\u3400-\u9fff]/u

const enclosingCall = (node) => {
  let current = node.parent
  while (current && !ts.isCallExpression(current) && !ts.isStatement(current)) {
    current = current.parent
  }
  return current && ts.isCallExpression(current) ? current : null
}

const isConsoleDiagnostic = (node) => {
  const call = enclosingCall(node)
  return Boolean(
    call
      && ts.isPropertyAccessExpression(call.expression)
      && ts.isIdentifier(call.expression.expression)
      && call.expression.expression.text === 'console',
  )
}

const isLogicMatcher = (node) => {
  const parent = node.parent
  if (parent && ts.isBinaryExpression(parent)) {
    return [
      ts.SyntaxKind.EqualsEqualsToken,
      ts.SyntaxKind.EqualsEqualsEqualsToken,
      ts.SyntaxKind.ExclamationEqualsToken,
      ts.SyntaxKind.ExclamationEqualsEqualsToken,
    ].includes(parent.operatorToken.kind)
  }
  if (parent && ts.isCallExpression(parent) && ts.isPropertyAccessExpression(parent.expression)) {
    return ['includes', 'startsWith', 'endsWith', 'test'].includes(parent.expression.name.text)
  }
  return false
}

const isInsideJsx = (node) => {
  let current = node.parent
  while (current && !ts.isStatement(current) && !ts.isFunctionLike(current)) {
    if (ts.isJsxExpression(current) || ts.isJsxAttribute(current)) return true
    current = current.parent
  }
  return false
}

const isUserFacingCall = (node) => {
  const call = enclosingCall(node)
  if (!call) return false
  if (ts.isIdentifier(call.expression)) {
    return ['toast', 'alert', 'confirm'].includes(call.expression.text)
  }
  return false
}

const failures = []

for (const relativePath of migratedFiles) {
  const absolutePath = path.join(frontendRoot, relativePath)
  const sourceText = fs.readFileSync(absolutePath, 'utf8')
  const source = ts.createSourceFile(
    absolutePath,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    absolutePath.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  )

  const inspect = (node) => {
    const text = ts.isJsxText(node)
      ? node.getText(source).trim()
      : ts.isStringLiteralLike(node)
        ? node.text
        : null

    const userFacing = ts.isJsxText(node)
      || (ts.isStringLiteralLike(node) && (isInsideJsx(node) || isUserFacingCall(node)))

    if (text && hanPattern.test(text) && userFacing && !isLogicMatcher(node) && !isConsoleDiagnostic(node)) {
      const position = source.getLineAndCharacterOfPosition(node.getStart(source))
      failures.push(`${relativePath}:${position.line + 1}:${position.character + 1} ${text}`)
    }
    ts.forEachChild(node, inspect)
  }

  inspect(source)
}

if (failures.length > 0) {
  console.error('Migrated UI files contain untranslated Chinese literals:')
  for (const failure of failures) console.error(`- ${failure}`)
  process.exitCode = 1
} else {
  console.log(`i18n literal check passed for ${migratedFiles.length} migrated files`)
}
