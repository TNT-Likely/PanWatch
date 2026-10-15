export const qlib = {
  automation: {
    title: 'Automatic selection and paper trading (default)',
    start: 'Start automatic selection and paper trading',
    renew: 'Adjust / renew authorization',
    manual: 'Switch to manual',
    stop: 'Disable automatic mode',
    close: 'Cancel',
    authorize: 'Authorize and start automatic mode',
    flow: 'Update data → reviewed model selects stocks → freeze portfolio plan automatically → simulate at next open; follow status and results.',
    setup: 'Set up data and review a model once',
    account: 'Initialize paper account',
    consentNote:
      'Standing authorization removes daily confirmation. Regular non-ST CN main-board paper trades only; HK/US remain research. No brokers.',
    model: 'Current reviewed model: {{model}} ({{id}})',
    cashLimit: 'Maximum daily buy budget (CNY, including fees)',
    risk: 'Valid for 30 days, bound to model, initial funds and market allocations. Enables after-close data/ranking updates for configured markets and cancels pending plans, preserving history and holdings. Model/fund changes require new consent. Failed, stale or invalid data stops orders; no automatic model training/review. The app must remain running.',
    ack: 'I authorize recurring paper trades within this budget, understand research returns are unverified, and data failures may prevent fills.',
    bounds:
      'Model {{model}} · daily buy cap {{cap}} CNY incl. fees · expires {{expires}}',
    manualConfirm:
      'Switch to manual and pause the account? Cancel pending plans, preserve history and holdings; future plans need individual confirmation. Data-update settings remain unchanged.',
    stopConfirm:
      'Revoke standing authorization and pause? Pending plans are cancelled; existing positions are not liquidated.',
    phases: {
      needs_authorization:
        'Automatic is the default; first authorization is required. Upgrades do not silently activate it.',
      manual: 'Manual mode: confirm each plan.',
      paused: 'Authorized, but paper account is paused.',
      blocked: 'Automatic execution blocked; address the reason below.',
      waiting_data:
        'Waiting for valid data and current predictions; no stale catch-up orders.',
      ready:
        'Ready; the background planner will freeze this period automatically.',
      planned:
        'This period has been handled; inspect the waiting, fill or cancellation records below.',
    },
  },
  plan: {
    automaticOrigin: 'Generated automatically within standing authorization',
    manualOrigin: 'Individually confirmed plan',
    activeTitle: 'Pending paper plans',
    noActivePlans:
      'No pending plan. Completed plans remain in history and will not resume when the account starts.',
    historyTitle: 'Plan history ({{count}})',
    historyNote:
      'Filled, rejected, cancelled, superseded and expired plans remain for review. Starting the account does not rerun them. Showing the latest 25 plans only.',
    currency: 'CNY',
    itemCashLimit: 'Item cash cap incl. fees',
    keep: 'Keep holding',
    exclusionLimit:
      'Showing the first {{count}} exclusions; the total is shown above.',
    checkResult:
      'Plan check complete: {{opened}} buys, {{closed}} sells. Waiting or blocked plans remain visible below.',
    previewTitle: 'Preview paper plan',
    previewNote:
      'This is the whole model portfolio, not a purchase of this one stock. Previewing creates no orders; only listed actions execute after confirmation.',
    loading: 'Checking model, cash and holdings…',
    dataAsOf: 'Data as of {{date}}',
    scope: '{{ranked}} ranked / {{pool}} catalog stocks',
    setupAccount: 'Set paper funds and initialize the account',
    track: 'Track paper plans and execution',
    notInOrders:
      '{{symbol}} is not an action in this plan. Following or opening details never opens a position.',
    availableCash: 'Available CN cash',
    cashCeiling: 'Maximum buy budget including fees',
    estimatedFees: 'Reference-price fee estimate',
    executionDate: 'Planned execution window',
    stock: 'Security',
    action: 'Action',
    reference: 'Reference close',
    quantity: 'Estimated shares',
    fee: 'Estimated fee',
    buy: 'Buy',
    sell: 'Sell',
    filter: 'Validation',
    estimatesNote:
      'Shares and fees use the reference close, not guaranteed fills. Valid next-open quotes, 100-share lots and frozen budget constrain virtual fills; invalid quotes, price limits or insufficient cash reject actions without substituting other stocks.',
    keptAndExcluded:
      '{{kept}} existing Qlib positions retained; {{excluded}} securities excluded for scope, data or legacy-position conflicts.',
    exitNote:
      'Automatic mode rotates within standing consent; manual mode requires a new confirmed sell plan. Manual close remains subject to T+1. No price stop-loss/take-profit or target prices; legacy holdings remain separate.',
    pausedNote:
      'The paper account is paused. Confirmation will not execute it; start the account in paper trading when ready.',
    replaceNote:
      'This confirmation replaces the prior pending plan execution priority. Old plans and audit remain; old plans do not execute automatically.',
    ack: 'I confirm the whole action list and budget, understand that research has not validated effectiveness and estimated shares are not guaranteed fills.',
    repreview: 'Preview again',
    close: 'Close',
    confirm: 'Confirm this paper plan',
    confirming: 'Confirming…',
    confirmed:
      'Paper plan confirmed. Track waiting, paused or execution results in paper trading.',
    statusTitle: 'Confirmed paper plans',
    statusNote:
      'Following is a bookmark. Automatic mode uses standing budget consent; manual mode confirms each plan. Execution is only the next known CN open, 09:30–09:35; starting/checking never buys immediately.',
    refreshStatus: 'Refresh plan status',
    emptyPlans:
      'No executable plan yet. Authorize automatic mode once and wait for valid data, or switch to manual for per-plan confirmation. Ranks and bookmarks alone never authorize orders.',
    resultCount: '{{filled}} actions filled · {{rejected}} rejected',
    sourceDate: 'From {{date}} opportunity ranks',
    estimatedShares: 'Estimate {{count}} shares',
    sourceOpportunity: 'View source opportunity (read-only)',
    cancel: 'Cancel pending plan',
    cancelNote:
      'Cancel this unexecuted plan? Rankings and audit remain; holdings are not cleared and no older plan is resumed automatically.',
    checkPlans: 'Check pending plans',
    checkPlansShort: 'Check plans',
    states: {
      automation_blocked: 'Automatic execution blocked; action required',
      waiting_open: 'Waiting for next open',
      paused: 'Account paused; not executed',
      expired: 'Execution window missed; new plan required',
      cancelled: 'Cancelled; will not execute',
      model_changed: 'Model changed; confirmation required',
      superseded: 'Replaced by newer plan',
      filled: 'Virtually filled',
      rejected: 'All actions rejected',
      partially_filled: 'Some actions filled; others rejected',
      executed: 'Checked; no fill needed',
    },
    eventStates: { filled: 'Virtually filled', rejected: 'Rejected' },
    errors: {
      qlib_automation_request_key_required:
        'Reopen the authorization dialog for a fresh request identity.',
      qlib_automation_authorization_conflict:
        'This request identity was used or revoked. Reopen the dialog.',
      qlib_automation_inputs_unverified:
        'Frozen histories are absent or fail verification; automatic orders stopped.',
      qlib_automation_input_anomaly:
        'Adjusted change across 20 frozen observations exceeds the conservative 100% anomaly threshold. Review corporate actions/data, not an apparent return opportunity.',
      qlib_automation_authorization_required:
        'Standing authorization is no longer valid. Authorize again.',
      qlib_automation_authorization_expired:
        'Authorization expired; no new plans or automatic fills.',
      qlib_automation_account_paused:
        'Paper account paused; automatic planning and execution stopped.',
      qlib_automation_funds_changed:
        'Initial funds or allocations changed. Review the budget and authorize again.',
      qlib_automation_model_changed:
        'The active model changed; authorization never silently switches models.',
      qlib_automation_source_failed:
        'History synchronization has not succeeded. Automatic orders stop until data recovery.',
      qlib_automation_data_updating:
        'History is updating; new automatic plans and fills are temporarily blocked.',
      qlib_automation_prediction_required:
        'Waiting for predictions from the reviewed model; no automatic training or review.',
      qlib_automation_setup_required:
        'Set up authorized market data and approve a real-data model first.',
      qlib_automation_cash_limit_invalid:
        'Budget must be positive and at most 90% of allocated CN capital.',
      qlib_manual_confirmation_disabled_automatic:
        'Standing automatic mode is active; individual confirmation is unnecessary. Switch to manual first to choose plans yourself.',
      qlib_source_configuration_changed:
        'Source configuration changed; review and authorize again.',
      qlib_market_execution_not_supported:
        'HK/US currently support research and observation only, not paper fills.',
      qlib_unverified_execution_prices:
        'Provider snapshots are not certified execution prices; research only.',
      qlib_security_execution_not_supported:
        'Paper fills currently support ordinary non-ST CN main-board stocks only.',
      qlib_synthetic_execution_not_supported:
        'Synthetic examples test engineering, never real-security paper positions.',
      qlib_model_not_champion:
        'This is no longer the approved model. View current opportunities.',
      qlib_selection_not_latest:
        'These are older frozen ranks for review only; preview current opportunities.',
      qlib_prediction_stale:
        'Ranking data is stale; update before creating a plan.',
      qlib_prediction_coverage_insufficient:
        'Prediction coverage is insufficient for a paper plan.',
      qlib_training_cohort_not_full_prediction:
        'These are training-cohort ranks; predict all ready securities first.',
      qlib_execution_calendar_unknown:
        'The next session calendar is unknown; no execution date can be promised.',
      qlib_plan_expired:
        'This ranking execution window has passed. Generate new ranks and a new plan.',
      qlib_plan_already_confirmed:
        'These ranks already have a confirmation. Track the existing plan; no duplicate entry.',
      qlib_paper_account_required:
        'Initialize a paper account and set its funds first.',
      qlib_duplicate_managed_holdings:
        'Duplicate Qlib holdings require ledger review before preview.',
      qlib_no_changes_required:
        'No portfolio change is needed. No orders are invented to create activity.',
      insufficient_cash_or_lot:
        'Available cash cannot cover a whole lot and fees; not filled.',
      qlib_preview_changed_conflict:
        'Model, ranks, cash or holdings changed. Preview again before confirming; the old preview cannot execute.',
      qlib_research_ack_required:
        'Research mode requires explicit risk acknowledgement.',
      qlib_plan_preview_required:
        'Preview the plan before submitting a confirmation bound to that preview.',
      qlib_plan_already_executed_conflict:
        'The plan has executed and cannot be cancelled; manage positions in paper trading.',
      qlib_plan_holdings_changed:
        'Holding size or ownership changed after confirmation; this action was rejected.',
      legacy_position_not_managed:
        'Conflicts with a legacy non-Qlib position; no automatic takeover.',
      opening_quote_unavailable:
        'Valid opening price or volume is missing; no fill.',
      quote_stale_or_missing:
        'Current-session quote is missing or stale; no fill.',
      opening_price_limit:
        'Opening price reaches the configured limit; no fill.',
      paper_trading_t1_locked: 'CN T+1 has not unlocked; cannot sell.',
      paper_trading_open_time_missing:
        'Purchase time is missing; sellable shares cannot be verified.',
    },
  },
  universe: {
    qualityWarning:
      '{{count}} incompatible daily bars discarded; no invented prices',
    phases: {
      queued: 'Queued',
      catalog: 'Fetching full directories',
      catalog_complete: 'Directories complete',
      history: 'Synchronizing all histories',
      history_complete: 'Histories complete',
      prediction: 'Predicting all ready securities',
    },
    rebuild: 'Rebuild market history',
    rebuildConfirm:
      'Accept upstream historical revisions into new versions without deleting old artifacts? Changed training history requires retraining and review before switching rankings.',
    exclusionReason: 'Prediction exclusion reason',
    issues: {
      qlib_source_unavailable_resume_sync:
        'Upstream histories were repeatedly unavailable, so synchronization stopped. Completed data, models and ranks are preserved. Retry Resume unfinished sync later; no retraining or rebuild is needed.',
      qlib_universe_status_unavailable:
        'Research status is temporarily unavailable. Refresh later; no data was cleared.',
      qlib_universe_members_unavailable:
        'The stock directory is temporarily unavailable. Please retry later.',
      qlib_history_quality_insufficient:
        'Less than 95% of observed bars satisfy the contract',
      qlib_history_latest_bar_invalid:
        'Invalid prices in latest completed session',
      qlib_ohlc_invalid: 'OHLC fields violate the data contract',
      qlib_history_insufficient: 'Fewer than 220 valid trading sessions',
      qlib_history_stale_or_suspended:
        'Latest completed session absent, suspended or awaiting update',
      qlib_source_currency_mismatch: 'History currency differs from directory',
      qlib_history_revision_requires_review:
        'Revised price/adjustment history; rebuild, retrain and review',
      qlib_history_source_changed:
        'Source changed; rebuild and review required',
      qlib_calendar_mismatch: 'Dates disagree with observed local sessions',
      qlib_history_fetch_failed:
        'History request failed; synchronization is resumable',
      qlib_source_history_unavailable:
        'Provider has no history for this security',
      qlib_adjustment_dates_mismatch: 'Raw/adjusted date coverage differs',
      qlib_adjustment_ratio_inconsistent: 'Invalid adjustment ratio',
      qlib_adjustment_unavailable: 'Missing adjustment information',
      qlib_source_prices_invalid: 'Invalid source price or volume',
    },
    title: 'Full-market research universe',
    dailyHelp:
      'After initial model review and standing authorization, follow selection and paper results without daily training or confirmations. After-close updating alone controls data/ranks; recurring trades require the separate bounded authorization above.',
    maintenance: 'Data maintenance (settings, training, rebuilding)',
    maintenanceNote:
      'Setup, training and review are for first use or model changes. Resume interrupted downloads; rebuild only reviewed historical revisions, not ordinary network failures.',
    technicalDetails: 'Technical error code',
    start: 'Initialize full universe',
    configure: 'Universe settings',
    catalog: 'Refresh complete directory',
    browse: 'Browse full universe',
    history: 'Sync all market histories',
    train: 'Train market candidate',
    review: 'Review market model',
    predict: 'Predict all ready securities',
    cancel: 'Cancel task',
    resume: 'Resume synchronization',
    flow: 'All listed equities → resumable history synchronization → per-market training and review → one ranking across all ready securities. Tasks continue after leaving the page.',
    total: 'Catalog securities',
    ready: 'Latest history ready',
    predicted: 'Latest predictions',
    excluded: 'Not predictable yet',
    pending: 'Pending history',
    stale: 'Needs update',
    all: 'All statuses',
    coverageNote:
      'Pending {{pending}} · Needs update {{stale}} · Latest completed market session {{date}}. Latest predictions are not the catalog size.',
    currentOnly: 'Current listings, not certified historical membership',
    budgetNote:
      'Training budget {{count}} securities; prediction universe is not capped',
    autoOn: 'Per-market daily sync enabled',
    autoOff: 'Automatic sync disabled',
    sourceDisabled:
      'No compatible enabled daily source for {{market}}. Enable Tencent/Eastmoney for CN or Yahoo for HK/US in existing marketdata settings.',
    sources: 'Data sources',
    working:
      '{{market}} · {{phase}} · {{completed}} / {{total}}. Tasks can be cancelled; history sync is resumable.',
    training: 'Training a candidate; human review is still required.',
    failed: 'Action incomplete; previous models and rankings retained:',
    setup: 'Connect all three research universes',
    scope:
      'CN main board, ChiNext, STAR and BSE; HKEX equities; NASDAQ, NYSE and NYSE American common shares/partnership units and ADRs. Excludes ETFs, funds, warrants, OTC and delisted securities.',
    limitations:
      'Public sources offer no coverage guarantee. Gaps, short histories, suspension and currency mismatches are explicit. Yahoo HK/US historical quotes may already be split-adjusted: provider snapshots, not certified raw execution prices. Backtests are research proxies, never paper execution or formal validation.',
    budget: 'Training sample budget (3–256)',
    budgetHelp:
      'Deterministic board/exchange stratification bounds Alpha158 training memory. One approved model predicts every ready security in market-specific shards, not just the training cohort.',
    license: 'Data-use basis',
    consent:
      'I have the right to download and use these data. Public availability does not grant commercial use or redistribution rights.',
    automatic:
      'Sync and predict after completed sessions in each market timezone; never auto-train, approve or place orders.',
    close: 'Close',
    save: 'Save and fetch complete directories',
    cancelConfirm:
      'Cancel the task? Completed histories remain resumable and previous rankings will not be overwritten.',
    reviewNotice:
      'Approves research ranking only in this market; other models remain unchanged. No paper orders. Current-listed universes and snapshot adjustment have survivorship limitations; HK/US execution is not supported.',
    metrics:
      'Test Rank IC {{ic}} · Research backtest drawdown {{drawdown}} (not a return guarantee)',
    fullReport: 'View experiments and rolling validation',
    reason: 'Review rationale (at least 5 characters)',
    approve: 'Approve and predict every ready market security',
    memberNote:
      'Search and paginate the entire directory, including securities without price coverage, with explicit statuses and exclusion reasons. HK dual counters count as separate securities.',
    search: 'Search name or ticker',
    coverageFilter: 'History coverage',
    security: 'Security',
    board: 'Board / exchange',
    currency: 'Currency',
    state: 'Data status',
    loading: 'Loading…',
    page: 'Page {{page}} · {{total}} securities',
    previous: 'Previous',
    next: 'Next',
  },
  dataPipeline: {
    title: 'Market-data selection',
    start: 'Connect existing data',
    configure: 'Data settings',
    sync: 'Update history & ranks',
    train: 'Train candidate',
    review: 'Review candidate',
    flow: 'Existing marketdata → complete fixed pool → Qlib training → human review → stock rankings; opt-in updates after close.',
    notConfigured:
      'No real research pool initialized; this does not mean there are no market opportunities.',
    dataReady:
      'Market data ready. Rankings still use the reviewed model; new candidates need approval.',
    candidateReady: 'Candidate trained. Review it before using its rankings.',
    working:
      'Processing {{completed}} / {{total}} stocks · {{phase}}. Closing this page does not stop the job.',
    phases: {
      prediction: 'Generating reviewed-model ranks',
      calendar: 'Checking observed trading sessions',
      queued: 'Queued',
      quotes: 'Checking status',
      history: 'Fetching raw prices and adjustment snapshots',
      persist: 'Validating the complete pool',
      training: 'Training candidate model',
      complete: 'Data complete; checking model tasks',
    },
    poolStatus:
      'Fixed pool: {{count}} stocks · source {{vendor}} · after-close updates {{automatic}}',
    on: 'enabled',
    off: 'disabled',
    setupTitle: 'Connect existing marketdata',
    limits:
      'No new account. Reuses enabled Tencent / Eastmoney sources for 3–20 regular CN main-board stocks. Not historical index membership. Adjustment uses a disclosed hfq close-ratio proxy preserving raw intraday ratios, without historical ST/delisting certification. Research only.',
    pool: 'Fixed pool (space- or comma-separated symbols)',
    examples: 'Use example research pool',
    watchlist: 'Use my watchlist',
    poolCount: '{{count}} stocks (3–20 required)',
    vendor: 'History source',
    auto: 'Existing priority (one source for the whole pool)',
    license: 'Existing data-use basis',
    licensePlaceholder:
      'Describe your authorization, agreement or personal research basis',
    consent:
      'I confirm authorization under this basis. Public API access does not grant commercial use or redistribution rights.',
    automatic:
      'Enable trading-day after-close ingestion and prediction (18:00 by default). Periodic training produces candidates only, without automatic approval or orders.',
    invalidPool:
      'Enter 3–20 unique regular SH/SZ main-board symbols. ETFs, ST, STAR, ChiNext and short-history IPOs are not supported.',
    save: 'Save settings only',
    initialize: 'Initialize history and train',
    close: 'Cancel',
    cancel: 'Cancel download',
    cancelConfirm:
      'Cancel ingestion? The previous complete dataset and rankings will be preserved.',
    reinitializeConfirm:
      'Reinitialization creates a new dataset and candidate. Existing models and rankings remain until another review. Continue?',
    reviewNotice:
      'Research-mode approval is not a profit guarantee or formal validation. Fixed membership, public-source snapshots, historical status and adjustment proxy have limitations. Approval switches rankings only, without trades or paper decisions.',
    metrics:
      'Test Rank IC {{ic}} · maximum drawdown {{drawdown}} (research backtest, not a return guarantee)',
    fullReport: 'Inspect experiments, baseline and rolling validation',
    reviewReason: 'Review basis (at least 5 characters)',
    approve: 'Approve research model and show rankings',
    sourceDisabled:
      'No enabled compatible history source. Enable Tencent or Eastmoney K-lines in data-source settings.',
    stale:
      'History does not cover the latest completed session. Previous data retained; retry later.',
    poolUnavailable:
      'The pool contains unknown-status, ST, short-history or missing/suspended stocks. No partial update; check the pool.',
    historyChanged:
      'Source or frozen history changed. Append stopped. Reinitialize, retrain and review a new version.',
    workerUnavailable:
      'Qlib worker unavailable; inspect research runtime health.',
    fetchFailed:
      'Ingestion failed. Previous complete data and rankings retained; retry later.',
    failed:
      'Action incomplete. Previous data and reviewed rankings are retained:',
  },
  opportunityPage: {
    journey:
      'Update and select automatically → Freeze paper plan → Execute next open → Track results',
    journeyNote:
      'After initial model review and budget authorization, automatic mode needs no daily confirmation. Bad data stops orders. Manual mode remains available; following is always just a bookmark.',
    view: 'Opportunity view',
    featured: 'Current highlights',
    fullPool: 'All ranked stocks',
    advancedData: 'Research settings and full catalog coverage (advanced)',
    observableEvidence: 'Verifiable market context',
    priceContext: 'Adjusted change over 20 valid observations: {{value}}%',
    volumeContext:
      'Latest volume / preceding 20-observation average: {{value}}×',
    drawdownContext: 'From the last 60-observation close high: {{value}}%',
    contextUnavailable:
      'Verifiable market context is unavailable; no reasons are guessed from model scores.',
    evidenceNote:
      'Frozen market observations, not causal model explanations; adjustment or corporate-action anomalies may remain.',
    simulationState: 'Paper boundary',
    planPreviewRequired:
      'Preview the whole plan to check funds and execution conditions',
    title: 'Opportunities',
    subtitle:
      'Find stocks to research, filter model ranks, and open familiar stock details.',
    back: '← Back to opportunities',
    refresh: 'Refresh',
    research: 'Quant research',
    paper: 'View model portfolio',
    engine: 'Qlib model ranking',
    candidates: 'Current candidates',
    poolOnly: 'Imported research universe',
    watchedCandidates: 'Watched candidates',
    watchNote: 'Stocks in this universe on your watchlist',
    sampleFollows: 'Demo follows',
    sampleFollowNote: 'Research basket only; never your real watchlist',
    results: 'Filtered results',
    rankOnly: 'Ranks refer to the full frozen universe',
    dataDate: 'Data through',
    market: 'Market',
    hk: 'Hong Kong',
    us: 'United States',
    cn: 'China A-shares',
    source: 'Source',
    allSources: 'Full research universe',
    myWatchlist: 'My watchlist',
    holding: 'Real holdings',
    allHolding: 'All real holding states',
    held: 'Held',
    unheld: 'Not held',
    unknown: 'Status unavailable',
    sampleHolding: 'Demo has no real holding linkage',
    rankFilter: 'Rank range',
    allRanks: 'All ranks',
    top10: 'Top 10%',
    top20: 'Top 20%',
    top50: 'Top 50%',
    availability: 'Historical trading state',
    allAvailability: 'All historical trading states',
    tradable: 'Tradable in historical snapshot',
    blocked: 'Not tradable in historical snapshot',
    apply: 'Apply filters',
    clear: 'Clear filters',
    search: 'Search name or symbol',
    history: 'Selection date',
    latest: 'Latest selection',
    historyItem: 'Historical selection',
    historyNotice:
      'Historical frozen ranks are reference-only, not input for a new paper decision.',
    horizon: '{{days}} trading-day horizon',
    sample: 'Synthetic demo',
    marketData: 'Market data · provider declaration',
    sampleName: 'Demo stock {{code}}',
    sampleNotice:
      'Synthetic demo, not real companies or market opportunities. Following adds only to the demo research basket, never your real watchlist or holdings.',
    notice:
      'CN/HK/US use independent reviewed models, not win probabilities or buy advice. Universes cover current listings with explicit short-history/data exclusions. Current directories are not survivorship-free historical membership; market data is not formal validation.',
    candidate: 'Model portfolio candidate',
    observe: 'Observe',
    rank: '#{{rank}} of {{total}}',
    cardHint:
      'Research relative performance over {{days}} trading days; rank does not establish buyability.',
    referencePrice: 'Reference close (CNY)',
    rankChange: 'Rank change',
    rankDelta: '{{delta}} places (positive means higher)',
    openDetail: 'Open {{name}} details',
    follow: 'Follow',
    followed: 'Following',
    followLabel: 'Follow {{code}}',
    unfollowLabel: 'Unfollow {{code}}',
    sampleFollow: 'Follow demo',
    sampleFollowed: 'Following demo',
    followSaved: 'Follow state updated',
    unfollowConfirm: 'Unfollow {{name}}? Holdings will not be cleared.',
    method: 'Selection method and boundaries',
    methodNote:
      'Ranks come only from the reviewed Qlib model, never legacy scoring. Portfolio candidate means inclusion in a historical-rank reference set; actual paper targets remain subject to portfolio policy and ledger constraints. Entry ranges, stops and price targets are not generated. Raw model scores and inputs remain in details.',
    technical: 'Model inputs and version',
    detailNote:
      'These are frozen Qlib ranks. Quotes and other analysis are separate, not causal model explanations, and do not change the rank.',
    loadFailed: 'Failed to load',
    loading: 'Loading…',
    metadataUnavailable:
      'Watchlist or real holdings are unavailable; related filters and follow operations are restricted. Model ranking is unaffected.',
    workerUnavailable:
      'Model runtime unavailable; no new output can be generated and legacy scores are not restored.',
    setup: 'Set up data and a model in quant research',
    statuses: {
      ready: 'Selection updated',
      stale: 'Stale data · new paper decisions paused',
      no_champion: 'No reviewed model',
      no_prediction: 'No selection yet',
      history: 'Historical ranks · reference only',
      failed: 'Refresh failed · previous output is reference-only',
    },
    empty: {
      no_champion: 'Import data and review a model in quant research first.',
      no_prediction:
        'A model is available but no selection has been generated.',
      ready: 'No selection in this research universe.',
      stale: 'No historical selection to display.',
      history: 'This historical record is unavailable; choose another date.',
      failed: 'Selection could not be loaded; please retry.',
    },
    emptyFiltered: 'No stocks match. Try clearing the filters.',
  },
  title: 'Qlib research workbench',
  subtitle:
    'Traceable model ranks, held-out backtests and forward paper validation. CN daily, non-ST main-board stocks only.',
  tabs: {
    opportunities: 'Model opportunities',
    research: 'Research & backtests',
    governance: 'Model governance',
    data: 'Data & runtime',
    paper: 'Forward paper',
  },
  notice:
    'Model scores are neither win probabilities nor buy advice. Synthetic data validates engineering only. Fixed pools and snapshot adjustments cannot validate historical index selection.',
  refresh: 'Refresh',
  loading: 'Loading research records…',
  error: 'Operation failed. Check input, runtime or task status.',
  done: 'Operation completed',
  missingWorker:
    'Qlib worker unavailable. Selection is paused; there is no legacy-score fallback.',
  runtimeReady: 'Isolated runtime available',
  empty:
    'No research artifacts. Import data, run an experiment, then review a research model.',
  status: {
    no_champion: 'No reviewed model',
    no_prediction: 'No prediction',
    ready: 'Prediction ready',
    stale: 'Stale data: new paper decisions disabled',
  },
  dataKind: {
    market: 'Market data',
    synthetic: 'Synthetic · not market validation',
  },
  mode: { research: 'Research mode', validated: 'Pre-registered gates passed' },
  dataset: 'Research data version',
  chooseDataset: 'Choose data version',
  asof: 'Data through',
  model: 'Model version',
  horizon: 'Forecast horizon (sessions)',
  rank: 'Pool rank',
  percentile: 'Rank percentile',
  rawScore: 'Raw model score',
  rankChange: 'Rank change',
  tradable: 'Tradable at historical snapshot',
  blocked: 'Not tradable / missing',
  inputs: 'Inputs & lineage',
  inputNote:
    'Actual processed model inputs, not causal explanations, SHAP contributions or Agent interpretation.',
  basket: 'Research basket',
  addBasket: 'Add to research basket',
  removeBasket: 'Remove from research basket',
  history: 'Frozen prediction history',
  latest: 'Current reviewed model',
  decision: 'Create next-open paper decision',
  decisionConfirm:
    'Use these frozen ranks for a research paper decision? This does not validate effectiveness. Virtual opening-price fills run at 09:30–09:35 on the next known CN session. Invalid quotes, price limits and funding constraints reject fills. Legacy strategy positions are not managed.',
  decisionSaved: 'Decision frozen. View its execution date in Forward paper.',
  algorithm: 'Research preset',
  lightgbm: 'Alpha158 + LightGBM',
  ridge: 'Alpha158 + Ridge (transparent challenger)',
  topk: 'Portfolio stock count',
  rounds: 'Boosting rounds',
  train: 'Train & backtest',
  predict: 'Predict on selected data',
  templateName: 'Configuration name',
  saveTemplate: 'Save configuration',
  replay: 'Replay experiment',
  templates: 'Saved configurations',
  jobs: 'Background tasks',
  jobStatus: {
    queued: 'Queued',
    running: 'Running',
    succeeded: 'Succeeded',
    failed: 'Failed',
    cancelled: 'Cancelled',
  },
  cancel: 'Cancel task',
  cancelConfirm:
    'Cancel this task? Partial files cannot become publishable artifacts.',
  experiment: 'Immutable experiment',
  compare: 'Compare selected experiments',
  comparable: 'Same data, splits and execution conventions',
  incomparable: 'Different cohorts: not valid evidence of model superiority',
  export: 'Export report & config',
  rankIC: 'Held-out RankIC',
  icDays: 'Valid evaluation dates',
  netReturn: 'Net backtest return',
  drawdown: 'Max drawdown',
  baseline: '20-session momentum baseline',
  rolling: 'Independent rolling validation',
  splits: 'Time splits (purged labels)',
  splitLabels: { train: 'Train', valid: 'Validation', test: 'Test' },
  backtestNote:
    'Qlib historical backtest: long-only TopkDropout, next-day open, 100-share lots, 90% cash allocation and configured aggregate fees. Cash reference is not index excess return; daily bars do not model order-book impact. Backtest and forward paper equity stay separate.',
  fees: 'Execution & publication gates (saved before running)',
  openCost: 'Buy aggregate rate',
  closeCost: 'Sell aggregate rate',
  minCost: 'Minimum fee',
  maxDD: 'Allowed max drawdown',
  minIC: 'Minimum RankIC',
  minDays: 'Minimum evaluation dates',
  capital: 'Research initial capital',
  registry: 'Model registry & review',
  modelStates: {
    champion: 'Reviewed',
    candidate: 'Candidate',
    previous: 'Previously published',
    retired: 'Retired',
  },
  retire: 'Retire model',
  retireNote:
    'Retired models cannot predict, publish or roll back. Historical artifacts remain. Switch the reviewed model before retiring it.',
  publish: 'Review & publish',
  rollback: 'Roll back to this Qlib version',
  reason: 'Review reason (at least 5 characters)',
  publishMode: 'Use mode',
  publishNote:
    'Training never publishes automatically. Validated mode requires market data, historical membership, point-in-time adjustments, main and three rolling gates, plus momentum comparison. Rollback only selects a previously published Qlib version.',
  confirmPublish:
    'Approve this version? Historical predictions and fills remain unchanged.',
  schedule: 'Daily predictions & rolling training',
  scheduleHint:
    'Off by default. Connect existing marketdata on the opportunities page for automatic after-close ingestion, or import complete snapshots. Stale/revised history blocks updates. Prediction and training use separate queues; challengers remain shadow-only until human review.',
  enabled: 'Enable schedule',
  dailyHour: 'After-close run hour',
  retrainDays: 'Retrain interval (days)',
  saveSchedule: 'Save schedule',
  tick: 'Check schedule once',
  tickStates: {
    universe_busy: 'Full-universe task is running; no duplicate task created.',
    idle: 'Scheduled conditions not met. No task created.',
    data_stale:
      'Stale input: no task created. Import and select a completed snapshot.',
    queued: 'Task queued; candidates never publish automatically.',
    failed: 'Daily failure retry limit reached. Review task records.',
    cancelled: 'Task cancelled; no automatic retry today.',
    syncing:
      'Ingesting the complete pool from existing marketdata before prediction.',
    data_sync_failed:
      'Ingestion incomplete. Previous data and rankings retained; check data status on the opportunities page.',
  },
  monitoring: 'New-pipeline health',
  coverage: 'Prediction coverage',
  churn: 'Top-K turnover fraction',
  inputShift: 'Input-shift diagnostics',
  shiftNote:
    'Magnitude of robust training-fitted z scores, not a significance test; never auto-tunes or publishes.',
  audit: 'Review & task audit',
  source: 'Data source',
  license: 'Declared use license',
  upload: 'Import dataset JSON',
  importHint:
    'At least 220 completed sessions and three stocks. Explicit calendar, historical membership, unadjusted OHLCV and adjustment factors. Max 40MB / 200,000 rows. No Python, YAML or model uploads. Provider declarations do not grant data rights.',
  importConfirm:
    'Confirm your right to research this data and the accuracy of source, license and adjustment-time declarations.',
  badFile: 'Choose valid JSON no larger than 40MB.',
  hash: 'Content hash',
  noAutoData:
    'Connect complete CN/HK/US universes on Opportunities using enabled Tencent/Eastmoney/Yahoo sources. JSON supports other authorized data; declare HK board lots and use null price limits for HK/US.',
  paperTitle: 'Frozen decision → execution events → paper ledger',
  executionDate: 'Execution session',
  policy: 'Portfolio policy',
  targetWeights: 'Indicative target weights',
  executions: 'Virtual fills & rejections',
  side: 'Side',
  quantity: 'Quantity',
  price: 'Virtual fill price',
  reasonCode: 'Reason',
  lineage: 'Prediction / model / decision',
  forwardNet: 'Current model realized net P&L (CNY)',
  forwardOpenNet: 'Current model unrealized net P&L (CNY)',
  exposure: 'Qlib exposure / ledger equity',
  maxWeight: 'Largest Qlib position / equity',
  forwardMonitorNote:
    'Ledger observations: net P&L includes frozen fees and estimated exit costs. Exposure includes holdings from all Qlib versions, divided by total paper equity; legacy holdings are not Qlib exposure. These are not model returns or a historical backtest curve.',
  paperNote:
    'Only Qlib-lineaged holdings are managed. Legacy holdings remain available for manual handling, never erased or reassigned automatically. T+1, calendars, cash and fees are enforced by the paper ledger.',
  openPaper: 'Open paper ledger',
  positions: 'Current paper holdings',
  close: 'Close',
  comparison: 'Experiment comparison',
} as const
