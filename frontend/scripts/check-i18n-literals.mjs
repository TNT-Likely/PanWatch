import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const migratedFiles = [
  'src/App.tsx',
  'src/components/AccountMenu.tsx',
  'src/components/RouteBoundary.tsx',
  'src/pages/Login.tsx',
  'src/components/SelfCheckModal.tsx',
  'src/components/PatSection.tsx',
  'src/components/assistant/AgentPermissionsPanel.tsx',
  'src/pages/DataSources.tsx',
  'src/pages/Settings.tsx',
  'src/pages/Agents.tsx',
  'src/pages/Dashboard.tsx',
  'src/pages/PriceAlerts.tsx',
  'src/pages/Opportunities.tsx',
  'packages/biz-ui/src/components/price-alert-form-dialog.tsx',
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

    if (text && hanPattern.test(text) && !isConsoleDiagnostic(node)) {
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
