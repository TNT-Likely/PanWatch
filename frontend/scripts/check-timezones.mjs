import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sourceRoots = ['src', 'packages/api/src', 'packages/base-ui/src', 'packages/biz-ui/src']
const instantFields = new Set(['expire_at', 'expires_at', 'created_at', 'updated_at', 'closed_at', 'opened_at', 'generated_at', 'observed_at', 'publish_time', 'trigger_time', 'last_trigger_at'])

/** A narrow syntactic guard; entry tests also cover aliases and data flow. */
export function findTimezoneViolations(source, filename = 'component.tsx') {
  const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const failures = []
  const report = (node, message) => {
    const { line } = ast.getLineAndCharacterOfPosition(node.getStart(ast))
    failures.push(`${filename}:${line + 1} ${message}`)
  }
  const fieldName = node => {
    if (ts.isPropertyAccessExpression(node)) return node.name.text
    if (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression)) return node.argumentExpression.text
    if (ts.isIdentifier(node)) return node.text
    return undefined
  }
  const visit = node => {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && ['slice', 'substring', 'substr'].includes(node.expression.name.text)
      && instantFields.has(fieldName(node.expression.expression))) {
      report(node, 'Keep the instant offset; convert with a shared local-time helper before display or input')
    }
    if (ts.isPropertyAssignment(node) && node.name.getText(ast).replace(/['"]/g, '') === 'timeZone'
      && ts.isStringLiteral(node.initializer) && node.initializer.text !== 'UTC') {
      report(node, 'Resolve browser, deployment or exchange timezone explicitly instead of hardcoding a display zone')
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
    findTimezoneViolations(fs.readFileSync(file, 'utf8'), path.relative(frontendRoot, file)))
  if (failures.length) {
    console.error('Timezone check failed. See docs/timezones.md:')
    failures.forEach(failure => console.error(`- ${failure}`))
    process.exitCode = 1
  } else console.log('Timezone check passed')
}
