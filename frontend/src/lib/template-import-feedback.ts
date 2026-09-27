export interface TemplateImportSummary {
  updated_settings?: number
  created_ai_services?: number
  updated_ai_services?: number
  created_ai_models?: number
  updated_ai_models?: number
  created_notify_channels?: number
  updated_notify_channels?: number
  created_agents?: number
  updated_agents?: number
  created_stocks?: number
  updated_stocks?: number
  created_stock_agents?: number
  updated_stock_agents?: number
  created_accounts?: number
  updated_accounts?: number
  created_positions?: number
  updated_positions?: number
  dropped_ai_model_refs?: number
  dropped_notify_channel_refs?: number
}

const count = (value?: number) => Number(value || 0)

const formatCreatedAndUpdated = (label: string, created: number, updated: number) => {
  const changes: string[] = []
  if (created > 0) changes.push(`新增 ${created}`)
  if (updated > 0) changes.push(`更新 ${updated}`)
  return changes.length > 0 ? `${label}${changes.join('、')}` : null
}

export function buildTemplateImportFeedback(summary?: TemplateImportSummary) {
  const imported: string[] = []
  const updatedSettings = count(summary?.updated_settings)
  if (updatedSettings > 0) imported.push(`设置 ${updatedSettings} 项`)

  const aiServices = formatCreatedAndUpdated(
    'AI 服务',
    count(summary?.created_ai_services),
    count(summary?.updated_ai_services),
  )
  if (aiServices) imported.push(aiServices)

  const aiModels = formatCreatedAndUpdated(
    '模型',
    count(summary?.created_ai_models),
    count(summary?.updated_ai_models),
  )
  if (aiModels) imported.push(aiModels)

  const notifyChannels = formatCreatedAndUpdated(
    '通知渠道',
    count(summary?.created_notify_channels),
    count(summary?.updated_notify_channels),
  )
  if (notifyChannels) imported.push(notifyChannels)

  const stocks = formatCreatedAndUpdated(
    '关注标的',
    count(summary?.created_stocks),
    count(summary?.updated_stocks),
  )
  if (stocks) imported.push(stocks)

  const agents = formatCreatedAndUpdated(
    'Agent ',
    count(summary?.created_agents),
    count(summary?.updated_agents),
  )
  if (agents) imported.push(agents)

  const stockAgents = formatCreatedAndUpdated(
    '标的-Agent 绑定',
    count(summary?.created_stock_agents),
    count(summary?.updated_stock_agents),
  )
  if (stockAgents) imported.push(stockAgents)

  const accounts = formatCreatedAndUpdated(
    '账户',
    count(summary?.created_accounts),
    count(summary?.updated_accounts),
  )
  if (accounts) imported.push(accounts)

  const positions = formatCreatedAndUpdated(
    '持仓',
    count(summary?.created_positions),
    count(summary?.updated_positions),
  )
  if (positions) imported.push(positions)

  const skipped: string[] = []
  const droppedModels = count(summary?.dropped_ai_model_refs)
  const droppedChannels = count(summary?.dropped_notify_channel_refs)
  if (droppedModels > 0) skipped.push(`模型引用 ${droppedModels} 个`)
  if (droppedChannels > 0) skipped.push(`通知渠道引用 ${droppedChannels} 个`)

  return {
    successMessage: imported.length > 0
      ? `已导入：${imported.join('；')}`
      : '导入完成：没有需要新增或更新的内容',
    warningMessage: skipped.length > 0
      ? `未导入：${skipped.join('、')}（目标环境不存在对应配置）`
      : null,
  }
}
