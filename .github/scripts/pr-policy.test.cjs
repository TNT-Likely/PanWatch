'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {config, MARKER, evaluate, productionFile, nextState, readState, notice, run} = require('./pr-policy.cjs');
const SHA = 'a'.repeat(40);
const BODY = `## Background
A report action loses its selected symbol.
## Changes
Preserve the symbol when opening the report.
## Scope
One report action; unrelated flows are excluded.
## Validation
Tested commit: ${SHA}
Commands and results:
- node --test report.test.cjs: 3 passed
## Acceptance evidence
Steps: Open a temporary symbol and request a report.
Expected: The selected symbol is used.
Observed: The selected symbol is present in the report request.
Evidence: Sanitized request output: symbol=SPY, market=US.
## Boundaries and risks
Only this action changes; no migration or real notification.
## Follow-up
None`;
const makePr = changes => ({number: 1, title: 'fix(reports): preserve the selected symbol', body: BODY, head: {sha: SHA},
  base: {repo: {default_branch: 'main'}}, labels: [], changed_files: 1, state: 'open', created_at: '2020-01-01T00:00:00Z', html_url: 'https://github.com/example/repo/pull/1', ...changes});
const FILES = [{filename: 'src/report.py', additions: 10, deletions: 2}];
const NOW = new Date('2026-10-13T00:00:00Z');

for (const name of ['tests/test_prices.py', 'frontend/tests/view.test.tsx', 'src/__tests__/test.py', 'README.zh-CN.md', 'docs/demo.md', 'frontend/pnpm-lock.yaml', '.github/scripts/pr-policy.test.cjs']) {
  test(`exclude ${name} from production budget`, () => assert.equal(productionFile(name), false));
}
for (const name of ['src/api.py', 'frontend/src/View.tsx', 'frontend/src/i18n/locales/en-US/common.json', '.github/workflows/build.yml', 'Dockerfile']) {
  test(`include ${name} in production budget`, () => assert.equal(productionFile(name), true));
}
test('focused PR with current-head evidence passes', () => assert.deepEqual(evaluate(makePr(), FILES).problems, []));
test('title format requires scope and English', () => {
  for (const title of ['feat: add market', 'feat(market): 新增市场']) assert.match(evaluate(makePr({title}), FILES).problems.join(' '), /title/);
});
test('template-only evidence and comments fail', () => {
  const result = evaluate(makePr({body: BODY.replace('Observed: The selected symbol is present in the report request.', 'Observed: <!-- insert result -->')}), FILES);
  assert.match(result.problems.join(' '), /Observed/);
});
test('stale tested commit fails and short current SHA passes', () => {
  assert.match(evaluate(makePr({body: BODY.replace(SHA, 'b'.repeat(40))}), FILES).problems.join(' '), /current PR head/);
  assert.deepEqual(evaluate(makePr({body: BODY.replace(SHA, SHA.slice(0, 7))}), FILES).problems, []);
});
test('missing report sections fail', () => assert.match(evaluate(makePr({body: '## Background\nAn issue.'}), FILES).problems.join(' '), /Acceptance evidence/));
test('size sums additions and deletions; tests do not inflate it', () => {
  const files = [{filename: 'src/large.py', additions: 401, deletions: 400}, {filename: 'tests/test_big.py', additions: 2000, deletions: 0}];
  const pr = makePr({changed_files: 2});
  assert.match(evaluate(pr, files).problems.join(' '), /Scope exceeds/);
  assert.deepEqual(evaluate(pr, files, true).problems, []);
});
test('file count budget applies independently of line count', () => {
  const files = Array.from({length: 21}, (_, i) => ({filename: `src/${i}.py`, additions: 1, deletions: 0}));
  assert.match(evaluate(makePr({changed_files: 21}), files).problems.join(' '), /Scope exceeds/);
});
test('source renamed to documentation still counts', () => {
  const files = [{filename: 'docs/old.md', previous_filename: 'src/old.py', additions: 900, deletions: 0}];
  assert.equal(evaluate(makePr(), files).productionChanges, 900);
});
test('incomplete file API data fails safely', () => assert.throws(() => evaluate(makePr({changed_files: 2}), FILES), /Incomplete file list/));
test('scope exception does not waive maintainer evidence requests', () => {
  assert.match(evaluate(makePr({labels: [{name: config.labels.needsEvidence}]}), FILES, true).problems.join(' '), /maintainer requested acceptance/);
});
test('old PR gets a fresh full notice window', () => {
  const state = nextState({problems: ['missing'], hold: false}, {}, NOW);
  assert.equal(state.since, NOW.toISOString());
  assert.equal(state.close, false);
});
test('7-day reminder and 14-day closure retain original clock across updates', () => {
  const previous = {since: '2026-10-01T00:00:00Z'};
  assert.equal(nextState({problems: ['different'], hold: false}, previous, NOW).reminder, true);
  assert.equal(nextState({problems: ['different'], hold: false}, previous, NOW).close, false);
  assert.equal(nextState({problems: ['different'], hold: false}, previous, new Date('2026-10-15T00:00:00Z')).close, true);
});
test('hold and compliance clear the clock; releasing hold gets a new window', () => {
  const paused = nextState({problems: ['missing'], hold: true}, {since: '2020-01-01T00:00:00Z'}, NOW);
  assert.equal(paused.close, false);
  assert.equal(paused.since, null);
  assert.equal(nextState({problems: ['missing'], hold: false}, paused, NOW).since, NOW.toISOString());
  assert.equal(nextState({problems: [], hold: false}, {since: '2020-01-01T00:00:00Z'}, NOW).since, null);
});
test('only the policy bot can provide persisted state', () => {
  const body = `${MARKER}\n<!-- state:{"since":"2020-01-01T00:00:00Z"} -->`;
  assert.deepEqual(readState({user: {login: 'contributor'}, body}), {});
  assert.equal(readState({user: {login: 'github-actions[bot]'}, body}).since, '2020-01-01T00:00:00Z');
  assert.deepEqual(readState({user: {login: 'github-actions[bot]'}, body: `${MARKER}\n<!-- state:{"since":"invalid"} -->`}), {});
});

function harness({pr = makePr(), previous = null, latest = null, files = FILES} = {}) {
  let current = structuredClone(pr);
  let reads = 0;
  let comments = previous ? [{id: 11, html_url: 'https://github.com/example/repo/pull/1#issuecomment-11', user: {login: 'github-actions[bot]'}, body: notice({problems: ['missing'], productionFiles: 1, productionChanges: 12, hold: false}, previous, 'https://example.com/policy')}] : [];
  const writes = [];
  const write = (method, action) => async args => {writes.push({method, args}); return action ? action(args) : {data: {}};};
  const github = {rest: {
    pulls: {list: async () => [current], get: async () => ({data: structuredClone(++reads > 1 && latest ? latest : current)}), listFiles: async () => files,
      update: write('close', args => {current.state = args.state; return {data: current};})},
    issues: {getLabel: async () => ({data: {}}), listComments: async () => comments,
      addLabels: write('addLabels', args => {current.labels.push(...args.labels.map(name => ({name})));}),
      removeLabel: write('removeLabel', args => {current.labels = current.labels.filter(label => label.name !== args.name);}),
      updateComment: write('updateComment', args => {comments[0].body = args.body;}),
      createComment: write('createComment', args => {const comment = {id: 12, html_url: 'https://github.com/example/repo/pull/1#issuecomment-12', body: args.body, user: {login: 'github-actions[bot]'}}; comments.push(comment); return {data: comment};})},
    repos: {createCommitStatus: write('status')}
  }, paginate: async (fn, args) => fn(args)};
  const summary = {addHeading() {return this;}, addCodeBlock() {return this;}, async write() {}};
  return {github, context: {repo: {owner: 'example', repo: 'repo'}, serverUrl: 'https://github.com', payload: {}}, core: {info() {}, summary}, writes};
}
async function execute(h, mode = 'enforce') {
  const old = process.env.PR_POLICY_MODE;
  process.env.PR_POLICY_MODE = mode;
  try { await run(h); } finally {if (old === undefined) delete process.env.PR_POLICY_MODE; else process.env.PR_POLICY_MODE = old;}
}
const EXPIRED = {since: '2020-01-01T00:00:00Z', scopeSha: null};
test('audit performs no mutations even for expired invalid PRs', async () => {
  const h = harness({pr: makePr({body: ''}), previous: EXPIRED});
  await execute(h, 'audit');
  assert.deepEqual(h.writes, []);
});
test('first enforced notice never closes old backlog immediately', async () => {
  const h = harness({pr: makePr({body: ''})});
  await execute(h);
  assert.equal(h.writes.some(write => write.method === 'close'), false);
  assert.equal(h.writes.find(write => write.method === 'status').args.sha, SHA);
});
test('daily sweep closes unresolved PR after a real prior notice', async () => {
  const h = harness({pr: makePr({body: ''}), previous: EXPIRED});
  await execute(h);
  assert.equal(h.writes.filter(write => write.method === 'close').length, 1);
});
test('metadata events do not close PRs; sweep makes closure decision', async () => {
  const h = harness({pr: makePr({body: ''}), previous: EXPIRED});
  h.context.payload = {action: 'edited', pull_request: makePr({body: ''})};
  await execute(h);
  assert.equal(h.writes.some(write => write.method === 'close'), false);
});
test('reopening gets a fresh correction period', async () => {
  const h = harness({pr: makePr({body: ''}), previous: EXPIRED});
  h.context.payload = {action: 'reopened', pull_request: makePr({body: ''})};
  await execute(h);
  const state = readState({user: {login: 'github-actions[bot]'}, body: h.writes.find(write => write.method === 'updateComment').args.body});
  assert.ok(Date.parse(state.since) > Date.parse(EXPIRED.since));
});
test('a late hold or head/body update prevents closure', async () => {
  for (const changes of [{labels: [{name: config.labels.hold}]}, {head: {sha: 'b'.repeat(40)}}, {body: BODY}]) {
    const h = harness({pr: makePr({body: ''}), previous: EXPIRED, latest: makePr({body: '', ...changes})});
    await execute(h);
    assert.equal(h.writes.some(write => write.method === 'close'), false);
  }
});
test('hold does not make the merge status green', async () => {
  const h = harness({pr: makePr({labels: [{name: config.labels.hold}]})});
  await execute(h);
  assert.equal(h.writes.find(write => write.method === 'status').args.state, 'pending');
});
test('scope exception is bound to the approved head and revoked on a push', async () => {
  const h = harness({pr: makePr({labels: [{name: config.labels.scopeApproved}]}), previous: {...EXPIRED, scopeSha: 'b'.repeat(40)}});
  await execute(h);
  assert.equal(h.writes.some(write => write.method === 'removeLabel' && write.args.name === config.labels.scopeApproved), true);
});
test('scope exception cannot stop closure for missing acceptance evidence', async () => {
  const h = harness({pr: makePr({body: '', labels: [{name: config.labels.scopeApproved}]}), previous: {...EXPIRED, scopeSha: SHA}});
  await execute(h);
  assert.equal(h.writes.some(write => write.method === 'close'), true);
});
test('a compliant PR awaiting maintainer review is kept open', async () => {
  const h = harness({previous: EXPIRED});
  await execute(h);
  assert.equal(h.writes.some(write => write.method === 'close'), false);
  assert.equal(h.writes.find(write => write.method === 'status').args.state, 'success');
});

test('day-seven reminder is posted once and recorded in the persistent notice', async () => {
  const since = new Date(Date.now() - 8 * 86400000).toISOString();
  for (const alreadyReminded of [false, true]) {
    const h = harness({pr: makePr({body: ''}), previous: {since, scopeSha: null, remindedAt: alreadyReminded ? since : null}});
    await execute(h);
    const reminders = h.writes.filter(write => write.method === 'createComment' && write.args.body.startsWith('Reminder:'));
    assert.equal(reminders.length, alreadyReminded ? 0 : 1);
  }
});
test('stale label events cannot approve a newer head', async () => {
  const h = harness({pr: makePr({labels: [{name: config.labels.scopeApproved}]}), previous: EXPIRED});
  h.context.payload = {action: 'labeled', label: {name: config.labels.scopeApproved}, pull_request: makePr({head: {sha: 'b'.repeat(40)}})};
  await execute(h);
  assert.ok(h.writes.some(write => write.method === 'removeLabel'));
});
test('a new maintainer scope approval exempts size for exactly that head', async () => {
  const files = [{filename: 'src/large.py', additions: 1000, deletions: 0}];
  const pr = makePr({labels: [{name: config.labels.scopeApproved}]});
  const h = harness({pr, files});
  h.context.payload = {action: 'labeled', label: {name: config.labels.scopeApproved}, pull_request: pr};
  await execute(h);
  assert.equal(h.writes.find(write => write.method === 'status').args.state, 'success');
});

test('removing a hold grants a new window even if no paused sweep ran', async () => {
  const h = harness({pr: makePr({body: ''}), previous: EXPIRED});
  h.context.payload = {action: 'unlabeled', label: {name: config.labels.hold}, pull_request: makePr({body: ''})};
  await execute(h);
  const state = readState({user: {login: 'github-actions[bot]'}, body: h.writes.find(write => write.method === 'updateComment').args.body});
  assert.ok(Date.parse(state.since) > Date.parse(EXPIRED.since));
});
