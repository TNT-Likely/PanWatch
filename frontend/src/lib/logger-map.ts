// Map stable Python logger names to concise display names.
export const LOGGER_MAPPING_ZH: Record<string, string> = {
  // Agents
  'src.agents.daily_report': '收盘复盘',
  'src.agents.premarket_outlook': '盘前分析',
  'src.agents.intraday_monitor': '盘中监测',
  'src.agents.base': 'Agent执行链路',
  'src.agents.news_digest': '新闻速递',
  'src.agents.chart_analyst': '技术分析',
  'src.agents.tradingagents': '深度分析',
  'src.agents.tradingagents.agent': '深度分析-主流程',
  'src.agents.tradingagents.observability': '深度分析-进度与成本',
  'src.agents.tradingagents.data_context': '深度分析-数据上下文',
  'src.agents.tradingagents.toolkit_adapter': '深度分析-数据适配',
  'src.agents.tradingagents.decision': '深度分析-决策与模拟盘',
  'src.agents.tradingagents.runtime_support': '深度分析-运行时兼容',
  'src.agents.tradingagents.operations': '深度分析-触发与评估',
  'tradingagents': '深度分析(上游)',

  // Core
  'src.core.scheduler': '调度器',
  'src.core.ai_client': 'AI客户端',
  'src.core.notifier': '通知',
  'src.core.analysis_history': '分析历史',
  'src.core.suggestion_pool': '建议池',
  'src.core.data_collector': '数据采集',

  // Collectors
  'src.collectors.akshare_collector': '行情采集',
  'src.collectors.kline_collector': 'K线采集',
  'src.collectors.capital_flow_collector': '资金流采集',
  'src.collectors.news_collector': '新闻采集',
  'src.collectors.screenshot_collector': '截图采集',

  // Web/API
  'src.web.api': 'API',
  'src.web.app': 'Web应用',
  'src.web.database': '数据库',
  'src.web.stock_list': '股票列表',
  'api': 'API',

  // Entry
  'server': '服务',

  // Third-party & infra
  'httpx': 'HTTP客户端',
  'httpcore': 'HTTP内核',
  'urllib3': 'HTTP库',
  'requests': 'HTTP客户端',
  'uvicorn.access': '访问日志',
  'uvicorn.error': 'Uvicorn错误',
  'uvicorn': 'Uvicorn',
  'fastapi': 'FastAPI',
  'starlette': 'Starlette',
  'sqlalchemy.engine': '数据库引擎',
  'sqlalchemy': 'SQLAlchemy',
  'apscheduler': 'APScheduler',
  'playwright': '浏览器',
  'openai': 'AI SDK',
  'tenacity': '重试库',
}

export const LOGGER_MAPPING_EN: Record<string, string> = {
  'src.agents.daily_report': 'Closing review', 'src.agents.premarket_outlook': 'Pre-market analysis', 'src.agents.intraday_monitor': 'Intraday monitor', 'src.agents.base': 'Agent execution', 'src.agents.news_digest': 'News digest', 'src.agents.chart_analyst': 'Technical analysis', 'src.agents.tradingagents': 'Deep analysis', 'src.agents.tradingagents.agent': 'Deep analysis - main flow', 'src.agents.tradingagents.observability': 'Deep analysis - progress and cost', 'src.agents.tradingagents.data_context': 'Deep analysis - data context', 'src.agents.tradingagents.toolkit_adapter': 'Deep analysis - data adapter', 'src.agents.tradingagents.decision': 'Deep analysis - decisions and paper trading', 'src.agents.tradingagents.runtime_support': 'Deep analysis - runtime', 'src.agents.tradingagents.operations': 'Deep analysis - runs and evaluation', tradingagents: 'Deep analysis (upstream)',
  'src.core.scheduler': 'Scheduler', 'src.core.ai_client': 'AI client', 'src.core.notifier': 'Notifications', 'src.core.analysis_history': 'Analysis history', 'src.core.suggestion_pool': 'Suggestion pool', 'src.core.data_collector': 'Data collection',
  'src.collectors.akshare_collector': 'Quote collection', 'src.collectors.kline_collector': 'Chart collection', 'src.collectors.capital_flow_collector': 'Capital-flow collection', 'src.collectors.news_collector': 'News collection', 'src.collectors.screenshot_collector': 'Screenshot collection',
  'src.web.api': 'API', 'src.web.app': 'Web app', 'src.web.database': 'Database', 'src.web.stock_list': 'Stock list', api: 'API', server: 'Service',
  httpx: 'HTTP client', httpcore: 'HTTP core', urllib3: 'HTTP library', requests: 'HTTP client', 'uvicorn.access': 'Access log', 'uvicorn.error': 'Uvicorn errors', uvicorn: 'Uvicorn', fastapi: 'FastAPI', starlette: 'Starlette', 'sqlalchemy.engine': 'Database engine', sqlalchemy: 'SQLAlchemy', apscheduler: 'APScheduler', playwright: 'Browser', openai: 'AI SDK', tenacity: 'Retry library',
}

export function mapLoggerName(moduleName?: string, language = 'zh-CN'): string {
  if (!moduleName) return ''
  const mapping = language.toLowerCase().startsWith('en') ? LOGGER_MAPPING_EN : LOGGER_MAPPING_ZH
  let bestKey = ''
  for (const key of Object.keys(mapping)) {
    if (moduleName === key || moduleName.startsWith(key)) {
      if (key.length > bestKey.length) bestKey = key
    }
  }
  return mapping[bestKey] || moduleName
}

export function loggerOptions(language = 'zh-CN'): { key: string, label: string }[] {
  const mapping = language.toLowerCase().startsWith('en') ? LOGGER_MAPPING_EN : LOGGER_MAPPING_ZH
  return Object.entries(mapping).map(([key, label]) => ({ key, label }))
}
