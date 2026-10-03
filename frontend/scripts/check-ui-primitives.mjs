import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sourceRoots = ['src', 'packages/base-ui/src', 'packages/biz-ui/src']
const nativeDialogs = new Set(['confirm', 'alert', 'prompt'])
const scrollClass = /\boverflow(?:-[xy])?-(?:auto|scroll)\b/

export function findUiViolations(source, filename = 'component.tsx') {
  const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const failures = []
  const report = (node, message) => {
    const { line } = ast.getLineAndCharacterOfPosition(node.getStart(ast))
    failures.push(`${filename}:${line + 1} ${message}`)
  }
  const visit = node => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && nativeDialogs.has(node.expression.text)) {
      report(node, 'Use shared UI instead of a native browser dialog')
    }
    if (ts.isPropertyAccessExpression(node) && ts.isIdentifier(node.expression)
      && ['window', 'globalThis'].includes(node.expression.text) && nativeDialogs.has(node.name.text)) {
      report(node, 'Use shared UI instead of a native browser dialog')
    }
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(ast)
      if (['select', 'option', 'dialog'].includes(tag)) report(node, 'Use shared Select or Dialog components')
      if (/^[a-z]/.test(tag)) {
        const attribute = node.attributes.properties.find(value => ts.isJsxAttribute(value) && value.name.getText(ast) === 'className')
        const classes = attribute?.initializer?.getText(ast) ?? ''
        if (scrollClass.test(classes) && !/\bscrollbar(?:-none)?\b/.test(classes)) {
          report(attribute, 'Scrollable regions need scrollbar (or intentional scrollbar-none)')
        }
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(ast)
  return failures
}

function collect(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const absolute = path.join(directory, entry.name)
    if (entry.isDirectory()) return collect(absolute)
    return /\.(ts|tsx)$/.test(entry.name) && !/\.(test|spec)\.(ts|tsx)$/.test(entry.name) ? [absolute] : []
  })
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const failures = sourceRoots.flatMap(root => collect(path.join(frontendRoot, root))).sort().flatMap(file =>
    findUiViolations(fs.readFileSync(file, 'utf8'), path.relative(frontendRoot, file)))
  if (failures.length) {
    console.error('UI primitive check failed. See UI_GUIDELINES.md:')
    failures.forEach(failure => console.error(`- ${failure}`))
    process.exitCode = 1
  } else console.log('UI primitive check passed')
}
