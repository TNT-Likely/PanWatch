'use strict';

const config = require('../pr-policy.json');
const MARKER = '<!-- panwatch-pr-policy:v1 -->';
const DAY = 86400000;
const SECTIONS = ['Background', 'Changes', 'Scope', 'Validation', 'Acceptance evidence', 'Boundaries and risks', 'Follow-up'];

function substantive(text) {
  const clean = text.replace(/<!--[\s\S]*?-->/g, '').replace(/\[[ xX]\]/g, '').trim();
  return clean.length > 0 && !/^(?:[-*\s]*|TODO|TBD|N\/?A|None|<[^>]+>|\.{3})[.!\s]*$/i.test(clean);
}

function sections(body) {
  const clean = (body || '').replace(/<!--[\s\S]*?-->/g, '');
  const headings = [...clean.matchAll(/^##\s+(.+?)\s*$/gm)];
  return Object.fromEntries(headings.map((match, index) => [match[1].toLowerCase(), clean.slice(match.index + match[0].length, headings[index + 1]?.index).trim()]));
}

function field(text, name) {
  const pattern = new RegExp(`^\\s*(?:[-*]\\s+)?${name}:\\s*([\\s\\S]*?)(?=^\\s*(?:[-*]\\s+)?[A-Za-z][A-Za-z ]+:|$(?![\\s\\S]))`, 'mi');
  return text.match(pattern)?.[1]?.trim() || '';
}

function productionFile(path) {
  return !(/(^|\/)(tests?|__tests__|docs)\//.test(path)
    || /(^|\/)(?:test_[^/]+\.py|[^/]+\.(?:test|spec)\.[cm]?[jt]sx?)$/.test(path)
    || /\.md$/i.test(path)
    || /(^|\/)(?:pnpm-lock\.yaml|package-lock\.json|yarn\.lock|uv\.lock|poetry\.lock)$/.test(path));
}

function evaluate(pr, files, scopeApproved = false) {
  if (pr.changed_files !== files.length) throw new Error(`Incomplete file list for #${pr.number}: ${files.length}/${pr.changed_files}`);
  const production = files.filter(file => productionFile(file.filename) || (file.previous_filename && productionFile(file.previous_filename)));
  const changes = production.reduce((sum, file) => sum + file.additions + file.deletions, 0);
  const problems = [];
  if (!/^(?:feat|fix|refactor|perf|test|docs|chore|build|ci|hotfix|release)\([a-z0-9][a-z0-9-]*\): [\x20-\x7e]+$/.test(pr.title)) {
    problems.push('Use an English Conventional Commit title: type(scope): subject.');
  }
  const parts = sections(pr.body);
  for (const name of SECTIONS) {
    const value = parts[name.toLowerCase()] || '';
    if (name === 'Follow-up' ? !value.trim() : !substantive(value)) problems.push(`Fill the "${name}" section; comments/placeholders do not count.`);
  }
  const validation = parts.validation || '';
  const tested = field(validation, 'Tested commit').replace(/`/g, '');
  if (!/^[a-f0-9]{7,40}$/i.test(tested) || !pr.head.sha.startsWith(tested.toLowerCase())) {
    problems.push('Validation must name the current PR head in "Tested commit:" (7–40 hexadecimal characters).');
  }
  if (!substantive(field(validation, 'Commands and results'))) problems.push('Validation needs "Commands and results:" with actual outcomes, or a justified non-applicability statement.');
  for (const name of ['Steps', 'Expected', 'Observed', 'Evidence']) {
    if (!substantive(field(parts['acceptance evidence'] || '', name))) problems.push(`Acceptance evidence needs "${name}:"; link evidence or include sanitized output where appropriate.`);
  }
  if (!scopeApproved && (production.length > config.maxProductionFiles || changes > config.maxProductionChanges)) {
    problems.push(`Scope exceeds the budget: ${production.length} production files / ${changes} added+deleted lines; split into independently usable PRs or request a maintainer scope exception.`);
  }
  const labels = new Set(pr.labels.map(label => label.name));
  if (labels.has(config.labels.needsSplit)) problems.push('A maintainer requested a scope split (pr:needs-split); ask for re-review after narrowing the PR.');
  if (labels.has(config.labels.needsEvidence)) problems.push('A maintainer requested acceptance evidence (pr:needs-evidence); ask for re-review after supplying it.');
  return {problems, productionFiles: production.length, productionChanges: changes, hold: labels.has(config.labels.hold)};
}

function nextState(result, previous = {}, now = new Date(), scopeSha = null) {
  const since = result.problems.length && !result.hold ? (previous.since || now.toISOString()) : null;
  const age = since ? (now - new Date(since)) / DAY : 0;
  return {since, scopeSha, remindedAt: since && since === previous.since ? previous.remindedAt || null : null, reminder: age >= config.reminderDays, close: age >= config.closeDays && !result.hold};
}

function readState(comment) {
  if (comment?.user?.login !== 'github-actions[bot]' || !comment.body?.startsWith(MARKER)) return {};
  const raw = comment.body.match(/<!-- state:(\{[^\n]+\}) -->/)?.[1];
  try {
    const state = JSON.parse(raw || '{}');
    if (state.since && !Number.isFinite(Date.parse(state.since))) return {};
    return state;
  } catch { return {}; }
}

function notice(result, state, policyUrl) {
  const message = result.hold ? 'A maintainer has put this PR on hold. Automatic closure is paused; merge readiness is still checked.'
    : result.problems.length ? 'This PR needs changes before review.' : 'The mechanical PR requirements are satisfied. Product acceptance, evidence quality and code review remain maintainer decisions.';
  const deadline = state.since ? new Date(Date.parse(state.since) + config.closeDays * DAY).toISOString() : null;
  return [MARKER, `<!-- state:${JSON.stringify({since: state.since, scopeSha: state.scopeSha, remindedAt: state.remindedAt || null})} -->`, '### PanWatch contribution check', message,
    `Scope: ${result.productionFiles} production files, ${result.productionChanges} added + deleted production lines. Tests, Markdown docs and known lockfiles are excluded.`,
    ...result.problems.map(problem => `- ${problem}`),
    ...(deadline ? [`${state.reminder ? '**Reminder:** ' : ''}Please resolve the items by **${deadline}** (${config.closeDays} days from the first notice). Comments and pushes do not extend this deadline. A maintainer may apply \`pr:hold\` when more time is justified; removing it grants a fresh window.`,
      'If unresolved, this PR will be closed on a daily sweep. The branch is preserved; reopen or submit smaller PRs when ready. Existing PRs receive the same full notice period.'] : []),
    `[Contribution policy](${policyUrl})`, '本检查只核对材料与改动规模，不代表产品或实现已经验收。首次提示后有14天整改窗口；第7天提醒，逾期仍未达标自动关闭。维护者可用 pr:hold 暂停；关闭后仍可重开或提交拆分版本。'
  ].join('\n\n');
}

async function run({github, context, core}) {
  const mode = process.env.PR_POLICY_MODE || 'audit';
  if (!['audit', 'enforce'].includes(mode)) throw new Error(`Unknown PR_POLICY_MODE: ${mode}`);
  const repo = context.repo;
  const mutate = mode === 'enforce';
  const sweep = !context.payload.pull_request;
  if (mutate) {
    for (const name of Object.values(config.labels)) {
      try { await github.rest.issues.getLabel({...repo, name}); }
      catch (error) {
        if (error.status !== 404) throw error;
        await github.rest.issues.createLabel({...repo, name, color: 'fbca04', description: 'PanWatch contribution policy; see docs/pr-policy.md'});
      }
    }
  }
  const prs = sweep ? await github.paginate(github.rest.pulls.list, {...repo, state: 'open', per_page: 100}) : [context.payload.pull_request];
  const summary = [];
  for (const candidate of prs) {
    const {data: pr} = await github.rest.pulls.get({...repo, pull_number: candidate.number});
    if (pr.state !== 'open') continue;
    const files = await github.paginate(github.rest.pulls.listFiles, {...repo, pull_number: pr.number, per_page: 100});
    const comments = await github.paginate(github.rest.issues.listComments, {...repo, issue_number: pr.number, per_page: 100});
    const comment = comments.find(item => item.user?.login === 'github-actions[bot]' && item.body?.startsWith(MARKER));
    const previous = readState(comment);
    const labels = new Set(pr.labels.map(label => label.name));
    const justApproved = context.payload.action === 'labeled' && context.payload.label?.name === config.labels.scopeApproved && context.payload.pull_request?.head.sha === pr.head.sha;
    const scopeSha = labels.has(config.labels.scopeApproved) && (justApproved || previous.scopeSha === pr.head.sha) ? pr.head.sha : null;
    const result = evaluate(pr, files, Boolean(scopeSha));
    const renewed = context.payload.action === 'reopened' || (context.payload.action === 'unlabeled' && context.payload.label?.name === config.labels.hold);
    const state = nextState(result, renewed ? {...previous, since: null} : previous, new Date(), scopeSha);
    summary.push(`#${pr.number}: ${result.problems.length} item(s), ${result.productionFiles} files / ${result.productionChanges} lines${state.close ? ', due for closure' : ''}`);
    if (!mutate) continue;
    // A scope exception covers one reviewed head; a new push must be reviewed again.
    if (labels.has(config.labels.scopeApproved) && !scopeSha) await github.rest.issues.removeLabel({...repo, issue_number: pr.number, name: config.labels.scopeApproved});
    if (result.problems.length && !labels.has(config.labels.needsWork)) await github.rest.issues.addLabels({...repo, issue_number: pr.number, labels: [config.labels.needsWork]});
    if (!result.problems.length && labels.has(config.labels.needsWork)) await github.rest.issues.removeLabel({...repo, issue_number: pr.number, name: config.labels.needsWork});
    const policyUrl = `${context.serverUrl}/${repo.owner}/${repo.repo}/blob/${pr.base.repo.default_branch}/docs/pr-policy.md`;
    if (sweep && state.reminder && !state.remindedAt && !state.close) {
      await github.rest.issues.createComment({...repo, issue_number: pr.number, body: 'Reminder: this PR still has unresolved contribution requirements. Please follow the policy notice above before its 14-day deadline, or ask a maintainer for a hold.\n\n整改提醒：请在首次提示后的14天期限内解决上述事项；需要更多时间时可联系维护者暂停计时。'});
      state.remindedAt = new Date().toISOString();
    }
    const body = notice(result, state, policyUrl);
    let target = comment?.html_url || pr.html_url;
    if (comment && comment.body !== body) await github.rest.issues.updateComment({...repo, comment_id: comment.id, body});
    else if (!comment && (result.problems.length || scopeSha || result.hold)) {
      const {data: created} = await github.rest.issues.createComment({...repo, issue_number: pr.number, body});
      target = created.html_url;
    }
    await github.rest.repos.createCommitStatus({...repo, sha: pr.head.sha, context: config.statusContext,
      state: result.hold ? 'pending' : result.problems.length ? 'failure' : 'success', target_url: target,
      description: result.hold ? 'Maintainer hold; automatic closure paused' : result.problems.length ? `${result.problems.length} contribution requirement(s) need attention` : 'Materials and scope checked; maintainer review required'});
    if (sweep && state.close) {
      // Re-fetch metadata and files before closing: never close on a stale snapshot.
      const {data: latest} = await github.rest.pulls.get({...repo, pull_number: pr.number});
      const fingerprint = item => JSON.stringify([item.head.sha, item.base.sha, item.changed_files, item.title, item.body, item.labels.map(label => label.name).sort()]);
      // The needs-work label was added above; ignore only that bot-owned label.
      const withoutOwnedLabel = item => ({...item, labels: item.labels.filter(label => label.name !== config.labels.needsWork && label.name !== config.labels.scopeApproved)});
      if (latest.state !== 'open' || fingerprint(withoutOwnedLabel(latest)) !== fingerprint(withoutOwnedLabel(pr))) continue;
      if (latest.labels.some(label => label.name === config.labels.hold) || (!scopeSha && latest.labels.some(label => label.name === config.labels.scopeApproved))) continue;
      await github.rest.issues.createComment({...repo, issue_number: pr.number, body: 'Closing after the 14-day contribution notice: the items above remain unresolved. This preserves your branch and work. Please reopen after addressing them, or submit independently usable smaller PRs.\n\n整改提示已满14天，以上事项仍未解决，因此关闭此PR。分支与改动保留；补齐验收材料后可以重开，也欢迎按独立可用的范围拆分提交。'});
      await github.rest.pulls.update({...repo, pull_number: pr.number, state: 'closed'});
    }
  }
  core.info(summary.join('\n'));
  await core.summary.addHeading(`PR policy: ${mode}`).addCodeBlock(summary.join('\n'), 'text').write();
}

module.exports = {config, MARKER, sections, field, productionFile, evaluate, nextState, readState, notice, run};
