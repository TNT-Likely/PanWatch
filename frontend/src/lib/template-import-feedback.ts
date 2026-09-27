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
type Translate = (key: string, options?: Record<string, unknown>) => string

const formatCreatedAndUpdated = (label: string, created: number, updated: number, tr: Translate) => {
  const changes: string[] = []
  if (created > 0) changes.push(tr('configuration:settingsPage.importFeedback.created', { count: created }))
  if (updated > 0) changes.push(tr('configuration:settingsPage.importFeedback.updated', { count: updated }))
  return changes.length > 0
    ? tr('configuration:settingsPage.importFeedback.resource', { label, changes: changes.join(tr('configuration:settingsPage.importFeedback.listSeparator')) })
    : null
}

export function buildTemplateImportFeedback(summary: TemplateImportSummary | undefined, tr: Translate) {
  const imported: string[] = []
  const updatedSettings = count(summary?.updated_settings)
  if (updatedSettings > 0) imported.push(tr('configuration:settingsPage.importFeedback.settings', { count: updatedSettings }))

  const aiServices = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.aiServices'),
    count(summary?.created_ai_services),
    count(summary?.updated_ai_services),
    tr,
  )
  if (aiServices) imported.push(aiServices)

  const aiModels = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.models'),
    count(summary?.created_ai_models),
    count(summary?.updated_ai_models),
    tr,
  )
  if (aiModels) imported.push(aiModels)

  const notifyChannels = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.notifyChannels'),
    count(summary?.created_notify_channels),
    count(summary?.updated_notify_channels),
    tr,
  )
  if (notifyChannels) imported.push(notifyChannels)

  const stocks = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.stocks'),
    count(summary?.created_stocks),
    count(summary?.updated_stocks),
    tr,
  )
  if (stocks) imported.push(stocks)

  const agents = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.agents'),
    count(summary?.created_agents),
    count(summary?.updated_agents),
    tr,
  )
  if (agents) imported.push(agents)

  const stockAgents = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.stockAgents'),
    count(summary?.created_stock_agents),
    count(summary?.updated_stock_agents),
    tr,
  )
  if (stockAgents) imported.push(stockAgents)

  const accounts = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.accounts'),
    count(summary?.created_accounts),
    count(summary?.updated_accounts),
    tr,
  )
  if (accounts) imported.push(accounts)

  const positions = formatCreatedAndUpdated(
    tr('configuration:settingsPage.importFeedback.labels.positions'),
    count(summary?.created_positions),
    count(summary?.updated_positions),
    tr,
  )
  if (positions) imported.push(positions)

  const skipped: string[] = []
  const droppedModels = count(summary?.dropped_ai_model_refs)
  const droppedChannels = count(summary?.dropped_notify_channel_refs)
  if (droppedModels > 0) skipped.push(tr('configuration:settingsPage.importFeedback.droppedModels', { count: droppedModels }))
  if (droppedChannels > 0) skipped.push(tr('configuration:settingsPage.importFeedback.droppedChannels', { count: droppedChannels }))

  return {
    successMessage: imported.length > 0
      ? tr('configuration:settingsPage.importFeedback.success', { details: imported.join(tr('configuration:settingsPage.importFeedback.itemSeparator')) })
      : tr('configuration:settingsPage.importFeedback.empty'),
    warningMessage: skipped.length > 0
      ? tr('configuration:settingsPage.importFeedback.warning', { details: skipped.join(tr('configuration:settingsPage.importFeedback.listSeparator')) })
      : null,
  }
}
