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
const sourceArgumentIndex = process.argv.indexOf('--source')
const requestedSource = sourceArgumentIndex >= 0 ? process.argv[sourceArgumentIndex + 1] : null
const migratedFiles = requestedSource
  ? [requestedSource]
  : [
      ...collectSourceFiles(path.join(frontendRoot, 'src')),
      ...collectSourceFiles(path.join(frontendRoot, 'packages', 'api', 'src')),
      ...collectSourceFiles(path.join(frontendRoot, 'packages', 'biz-ui', 'src')),
    ].sort()
const localeNames = ['zh-CN', 'en-US']

const unwrapExpression = (expression) => {
  let current = expression
  while (current && (
    ts.isAsExpression(current)
    || ts.isSatisfiesExpression(current)
    || ts.isParenthesizedExpression(current)
    || ts.isNonNullExpression(current)
    || ts.isTypeAssertionExpression(current)
  )) {
    current = current.expression
  }
  return current
}

const propertyNameText = (name) => (
  ts.isIdentifier(name) || ts.isStringLiteralLike(name) || ts.isNumericLiteral(name)
    ? name.text
    : null
)

const collectResourcePaths = (expression, prefix, catalog) => {
  const value = unwrapExpression(expression)
  if (!value || !ts.isObjectLiteralExpression(value)) {
    if (prefix) catalog.leaves.add(prefix)
    return
  }
  if (prefix) catalog.branches.add(prefix)
  for (const property of value.properties) {
    if (!ts.isPropertyAssignment(property)) continue
    const name = propertyNameText(property.name)
    if (!name) continue
    const childPath = prefix ? `${prefix}.${name}` : name
    collectResourcePaths(property.initializer, childPath, catalog)
  }
}

const loadResourceCatalog = (locale) => {
  const localeDirectory = path.join(frontendRoot, 'src', 'i18n', 'locales', locale)
  const namespaces = new Map()
  for (const entry of fs.readdirSync(localeDirectory, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith('.ts')) continue
    const absolutePath = path.join(localeDirectory, entry.name)
    const source = ts.createSourceFile(
      absolutePath,
      fs.readFileSync(absolutePath, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    )
    for (const statement of source.statements) {
      if (!ts.isVariableStatement(statement)) continue
      for (const declaration of statement.declarationList.declarations) {
        if (!ts.isIdentifier(declaration.name) || !declaration.initializer) continue
        const catalog = { leaves: new Set(), branches: new Set() }
        collectResourcePaths(declaration.initializer, '', catalog)
        namespaces.set(declaration.name.text, catalog)
      }
    }
  }
  return namespaces
}

const resourceCatalogs = new Map(localeNames.map((locale) => [locale, loadResourceCatalog(locale)]))
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
  || text.startsWith('text-market-')
  || text.startsWith('/')
  || text.includes('://')
  || text.includes('github.com/')
)

const failures = []
const keyFailures = []

const namespaceArguments = (call) => {
  const argument = call.arguments[0]
  if (!argument) return ['common']
  if (ts.isStringLiteralLike(argument)) return [argument.text]
  if (ts.isArrayLiteralExpression(argument)) {
    const namespaces = argument.elements
      .filter(ts.isStringLiteralLike)
      .map((element) => element.text)
    return namespaces.length > 0 ? namespaces : ['common']
  }
  return ['common']
}

const bindingNames = (name) => {
  if (ts.isIdentifier(name)) return [name.text]
  if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) {
    return name.elements.flatMap((element) => ts.isBindingElement(element) ? bindingNames(element.name) : [])
  }
  return []
}

const translatorForKey = (binding, key) => {
  const rawKey = `${binding.prefix}${key}`
  const separator = rawKey.indexOf(':')
  if (separator >= 0) {
    return {
      namespace: rawKey.slice(0, separator),
      path: rawKey.slice(separator + 1),
      prefix: '',
    }
  }
  return { namespace: binding.namespace, path: rawKey, prefix: '' }
}

const formatKeyFailure = (source, node, message) => {
  const position = source.getLineAndCharacterOfPosition(node.getStart(source))
  return `${path.relative(frontendRoot, source.fileName)}:${position.line + 1}:${position.character + 1} ${message}`
}

const validateResourcePath = (source, node, binding, key, dynamic) => {
  const target = translatorForKey(binding, key)
  const resourcePath = dynamic ? target.path.replace(/\.$/, '') : target.path
  if (!resourcePath) return
  for (const locale of localeNames) {
    const namespace = resourceCatalogs.get(locale)?.get(target.namespace)
    if (!namespace) {
      keyFailures.push(formatKeyFailure(source, node, `unknown ${locale} namespace "${target.namespace}"`))
      continue
    }
    const exists = dynamic
      ? namespace.branches.has(resourcePath)
      : namespace.leaves.has(resourcePath)
    if (!exists) {
      const kind = dynamic ? 'dynamic key prefix' : 'translation key'
      keyFailures.push(formatKeyFailure(
        source,
        node,
        `missing ${locale} ${kind} "${target.namespace}:${resourcePath}${dynamic ? '.*' : ''}"`,
      ))
    }
  }
}

const validateTranslationKeys = (source) => {
  const scopes = [new Map()]
  const currentScope = () => scopes[scopes.length - 1]
  const resolveBinding = (name) => {
    for (let index = scopes.length - 1; index >= 0; index -= 1) {
      if (scopes[index].has(name)) return scopes[index].get(name)
    }
    return null
  }

  const translatorFromCall = (call) => {
    const callee = unwrapExpression(call.expression)
    if (ts.isIdentifier(callee)) return resolveBinding(callee.text)
    if (ts.isPropertyAccessExpression(callee)
      && callee.name.text === 't'
      && ts.isIdentifier(callee.expression)
      && ['i18n', 'i18nInstance'].includes(callee.expression.text)) {
      return { namespace: 'common', prefix: '' }
    }
    return null
  }

  const returnedCall = (initializer) => {
    const value = unwrapExpression(initializer)
    if (!value || (!ts.isArrowFunction(value) && !ts.isFunctionExpression(value))) return null
    const body = unwrapExpression(value.body)
    if (body && ts.isCallExpression(body)) return { fn: value, call: body }
    if (!ts.isBlock(value.body)) return null
    const returned = value.body.statements.find(ts.isReturnStatement)
    const expression = returned?.expression ? unwrapExpression(returned.expression) : null
    return expression && ts.isCallExpression(expression) ? { fn: value, call: expression } : null
  }

  const deriveWrapperBinding = (initializer) => {
    const returned = returnedCall(initializer)
    if (!returned || returned.fn.parameters.length === 0) return null
    const keyParameter = returned.fn.parameters[0].name
    if (!ts.isIdentifier(keyParameter)) return null
    const sourceBinding = translatorFromCall(returned.call)
    const keyArgument = returned.call.arguments[0]
    if (!sourceBinding || !keyArgument) return null

    if (ts.isIdentifier(keyArgument) && keyArgument.text === keyParameter.text) return sourceBinding
    if (!ts.isTemplateExpression(keyArgument) || keyArgument.templateSpans.length !== 1) return null
    const span = keyArgument.templateSpans[0]
    if (!ts.isIdentifier(span.expression)
      || span.expression.text !== keyParameter.text
      || span.literal.text !== '') return null
    const target = translatorForKey(sourceBinding, keyArgument.head.text)
    return { namespace: target.namespace, prefix: target.path }
  }

  const registerDeclaration = (declaration) => {
    for (const name of bindingNames(declaration.name)) currentScope().set(name, null)
    if (!declaration.initializer) return
    const initializer = unwrapExpression(declaration.initializer)

    if (ts.isObjectBindingPattern(declaration.name)
      && initializer
      && ts.isCallExpression(initializer)
      && ts.isIdentifier(initializer.expression)
      && initializer.expression.text === 'useTranslation') {
      const namespace = namespaceArguments(initializer)[0]
      for (const element of declaration.name.elements) {
        const propertyName = element.propertyName ? propertyNameText(element.propertyName) : propertyNameText(element.name)
        if (propertyName === 't' && ts.isIdentifier(element.name)) {
          currentScope().set(element.name.text, { namespace, prefix: '' })
        }
      }
      return
    }

    if (!ts.isIdentifier(declaration.name) || !initializer) return
    if (ts.isIdentifier(initializer)) {
      const binding = resolveBinding(initializer.text)
      if (binding) currentScope().set(declaration.name.text, binding)
      return
    }
    if (ts.isCallExpression(initializer)
      && ts.isPropertyAccessExpression(initializer.expression)
      && initializer.expression.name.text === 'getFixedT') {
      const namespace = initializer.arguments[1]
      if (namespace && ts.isStringLiteralLike(namespace)) {
        currentScope().set(declaration.name.text, { namespace: namespace.text, prefix: '' })
      }
      return
    }
    const wrapper = deriveWrapperBinding(declaration.initializer)
    if (wrapper) currentScope().set(declaration.name.text, wrapper)
  }

  const validateCall = (call) => {
    const binding = translatorFromCall(call)
    const key = call.arguments[0]
    if (!binding || !key) return
    if (ts.isStringLiteralLike(key)) {
      validateResourcePath(source, key, binding, key.text, false)
      return
    }
    if (ts.isTemplateExpression(key) && key.head.text) {
      validateResourcePath(source, key, binding, key.head.text, true)
    }
  }

  const visit = (node) => {
    const createsScope = node !== source && (
      ts.isFunctionLike(node)
      || ts.isBlock(node)
      || ts.isCatchClause(node)
    )
    if (createsScope) {
      scopes.push(new Map())
      if (ts.isFunctionLike(node)) {
        for (const parameter of node.parameters) {
          for (const name of bindingNames(parameter.name)) currentScope().set(name, null)
        }
      } else if (ts.isCatchClause(node) && node.variableDeclaration) {
        for (const name of bindingNames(node.variableDeclaration.name)) currentScope().set(name, null)
      }
    }

    if (ts.isVariableDeclaration(node)) registerDeclaration(node)
    if (ts.isCallExpression(node)) validateCall(node)
    ts.forEachChild(node, visit)

    if (createsScope) scopes.pop()
  }

  visit(source)
}

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
  validateTranslationKeys(source)
}

if (failures.length > 0 || keyFailures.length > 0) {
  if (failures.length > 0) {
    console.error('Migrated UI files contain direct or indirect untranslated literals:')
    for (const failure of failures) console.error(`- ${failure}`)
  }
  if (keyFailures.length > 0) {
    console.error('Translation calls reference missing resource keys:')
    for (const failure of keyFailures) console.error(`- ${failure}`)
  }
  process.exitCode = 1
} else {
  console.log(`i18n literal and key checks passed for ${migratedFiles.length} migrated files`)
}
