import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const collectSourceFiles = (directory) => fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
  const absolutePath = path.join(directory, entry.name)
  if (entry.isDirectory()) return collectSourceFiles(absolutePath)
  if (!/\.(ts|tsx)$/.test(entry.name) || /\.(test|spec)\.(ts|tsx)$/.test(entry.name)) return []
  const relativePath = path.relative(frontendRoot, absolutePath)
  return relativePath.includes('/i18n/locales/') ? [] : [relativePath]
})

// App and shared-package code are both checked. Locale resources and tests are
// excluded because their literals are intentionally not rendered directly.
const migratedFiles = [
  ...collectSourceFiles(path.join(frontendRoot, 'src')),
  ...collectSourceFiles(path.join(frontendRoot, 'packages', 'api', 'src')),
  ...collectSourceFiles(path.join(frontendRoot, 'packages', 'biz-ui', 'src')),
].sort()
const hanPattern = /[\u3400-\u9fff]/u
const fixedEnglishFallbackPattern = /\b(?:build position|do not open|entry plan|unknown|watch)\b/i
const englishPhrasePattern = /\b[A-Za-z]{3,}(?:[ -][A-Za-z]{3,})+\b/
const singleWordEnglishPattern = /^[A-Za-z][A-Za-z0-9_-]{1,30}$/
const allowedDirectJsxTerms = new Set(['AI', 'HIT', 'MISS', 'PanWatch', 'PB', 'PE', 'ROE', 'TZ', 'ms'])
const displayPropertyNames = new Set([
  'aria-label', 'description', 'emptyText', 'helperText', 'hint', 'label',
  'message', 'placeholder', 'summary', 'title', 'tooltip',
])
const translatorNames = new Set(['configT', 'interfaceText', 'klineT', 'klineTr', 'oppT', 'stockT', 't', 'tr'])
const isTranslatorName = (name) => translatorNames.has(name) || /(?:T|Tr|Translate)$/.test(name)

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

const isInsidePresentationJsx = (node) => {
  let current = node.parent
  while (current && !ts.isStatement(current) && !ts.isFunctionLike(current)) {
    if (ts.isJsxAttribute(current)) return displayPropertyNames.has(current.name.text)
    if (ts.isJsxExpression(current)) {
      // A JSX expression that is not an attribute is rendered child content.
      if (!ts.isJsxAttribute(current.parent)) return true
    }
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

const isTranslationArgument = (node) => {
  let current = node.parent
  while (current && !ts.isCallExpression(current) && !ts.isStatement(current)) current = current.parent
  if (!current || !ts.isCallExpression(current)) return false
  if (ts.isIdentifier(current.expression)) return isTranslatorName(current.expression.text)
  return ts.isPropertyAccessExpression(current.expression)
    && isTranslatorName(current.expression.name.text)
}

const isDisplayProperty = (node) => {
  const parent = node.parent
  if (!parent || !ts.isPropertyAssignment(parent)) return false
  const name = parent.name
  const key = ts.isIdentifier(name) || ts.isStringLiteralLike(name) ? name.text : ''
  return displayPropertyNames.has(key)
}

const functionNameFor = (node) => {
  let current = node.parent
  while (current) {
    if (ts.isFunctionDeclaration(current) && current.name) return current.name.text
    if ((ts.isArrowFunction(current) || ts.isFunctionExpression(current)) && ts.isVariableDeclaration(current.parent)) {
      return ts.isIdentifier(current.parent.name) ? current.parent.name.text : ''
    }
    if (ts.isMethodDeclaration(current) && ts.isIdentifier(current.name)) return current.name.text
    current = current.parent
  }
  return ''
}

const isIndirectUserFacing = (node) => {
  if (isDisplayProperty(node)) return true
  const call = enclosingCall(node)
  if (call && ts.isIdentifier(call.expression) && call.expression.text === 'Error') return true
  let current = node.parent
  let returned = false
  while (current && !ts.isFunctionLike(current)) {
    if (ts.isJsxAttribute(current) || ts.isJsxElement(current) || ts.isJsxFragment(current)) return false
    if (ts.isReturnStatement(current)) {
      returned = true
      break
    }
    current = current.parent
  }
  if (!returned) return false
  const name = functionNameFor(node)
  return /(display|format|label|message|summary|text|title|description|hint|error)/i.test(name)
}

const isLocaleMapping = (node) => {
  let current = node.parent
  while (current) {
    if (ts.isVariableDeclaration(current) && ts.isIdentifier(current.name)) {
      return /_(ZH|EN)$/.test(current.name.text)
    }
    current = current.parent
  }
  return false
}

const looksLikePresentationLiteral = (node, text) => (
  hanPattern.test(text)
  || fixedEnglishFallbackPattern.test(text)
  || englishPhrasePattern.test(text)
  || (ts.isJsxText(node) && singleWordEnglishPattern.test(text))
)
const isTechnicalLiteral = (text) => (
  text === 'panwatch-locale'
  || text.startsWith('/')
  || text.includes('://')
  || text.includes('github.com/')
)

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
      || (ts.isStringLiteralLike(node) && (
        isInsidePresentationJsx(node) || isUserFacingCall(node) || isIndirectUserFacing(node)
      ))

    if (text && looksLikePresentationLiteral(node, text) && userFacing && !allowedDirectJsxTerms.has(text) && !isLogicMatcher(node)
      && !isConsoleDiagnostic(node) && !isTranslationArgument(node) && !isLocaleMapping(node)
      && !isTechnicalLiteral(text)) {
      const position = source.getLineAndCharacterOfPosition(node.getStart(source))
      failures.push(`${relativePath}:${position.line + 1}:${position.character + 1} ${text}`)
    }
    ts.forEachChild(node, inspect)
  }

  inspect(source)
}

if (failures.length > 0) {
  console.error('Migrated UI files contain direct or indirect untranslated literals:')
  for (const failure of failures) console.error(`- ${failure}`)
  process.exitCode = 1
} else {
  console.log(`i18n literal check passed for ${migratedFiles.length} migrated files`)
}
