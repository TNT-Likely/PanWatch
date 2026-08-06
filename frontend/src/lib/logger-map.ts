// Map Python module logger names to concise Chinese display names
export const LOGGER_MAPPING: Record<string, string> = {
  // Agents
  'src.agents.daily_report': '收盘复盘',
  'src.agents.premarket_outlook': '盘前分析',
  'src.agents.intraday_monitor': '盘中监测',
  'src.agents.base': 'Agent执行链路',
  'src.agents.news_digest': '新闻速递',
  'src.agents.chart_analyst': '技术分析',
  'src.agents.tradingagents': '深度分析',
  'src.agents.tradingagents.agent': '深度分析-主流程',
  'src.agents.tradingagents.progress': '深度分析-进度',
  'src.agents.tradingagents.portfolio_context': '深度分析-持仓上下文',
  'src.agents.tradingagents.toolkit_adapter': '深度分析-数据适配',
  'src.agents.tradingagents.paper_trading_bridge': '深度分析-模拟盘',
  'src.agents.tradingagents.cost_tracker': '深度分析-成本',
  'src.agents.tradingagents.llm_adapter': '深度分析-LLM',
  'src.agents.tradingagents.langchain_compat': '深度分析-兼容层',
  'src.agents.tradingagents.backfill': '深度分析-回填',
  'src.agents.tradingagents.result_mapper': '深度分析-结果映射',
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
  'src.web.api.forecast': '预测回测',
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

export function mapLoggerName(moduleName?: string): string {
  if (!moduleName) return ''
  let bestKey = ''
  for (const key of Object.keys(LOGGER_MAPPING)) {
    if (moduleName === key || moduleName.startsWith(key)) {
      if (key.length > bestKey.length) bestKey = key
    }
  }
  return LOGGER_MAPPING[bestKey] || moduleName
}

export function loggerOptions(): { key: string, label: string }[] {
  return Object.entries(LOGGER_MAPPING).map(([key, label]) => ({ key, label }))
}
