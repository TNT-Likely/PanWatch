import { describe, expect, it } from 'vitest'
import { buildTemplateImportFeedback } from '@/lib/template-import-feedback'

describe('buildTemplateImportFeedback', () => {
  it('separates imported resources from references missing in the target environment', () => {
    expect(buildTemplateImportFeedback({
      updated_settings: 5,
      created_agents: 0,
      updated_agents: 6,
      created_stocks: 14,
      updated_stocks: 5,
      created_stock_agents: 2,
      updated_stock_agents: 0,
      dropped_ai_model_refs: 2,
      dropped_notify_channel_refs: 8,
    })).toEqual({
      successMessage: '已导入：设置 5 项；关注标的新增 14、更新 5；Agent 更新 6；标的-Agent 绑定新增 2',
      warningMessage: '未导入：模型引用 2 个、通知渠道引用 8 个（目标环境不存在对应配置）',
    })
  })

  it('reports a no-op import without claiming resources were imported', () => {
    expect(buildTemplateImportFeedback({})).toEqual({
      successMessage: '导入完成：没有需要新增或更新的内容',
      warningMessage: null,
    })
  })

  it('includes AI, notification and portfolio resources in the success details', () => {
    expect(buildTemplateImportFeedback({
      created_ai_services: 1,
      created_ai_models: 2,
      created_notify_channels: 1,
      created_accounts: 1,
      created_positions: 3,
    })).toEqual({
      successMessage: '已导入：AI 服务新增 1；模型新增 2；通知渠道新增 1；账户新增 1；持仓新增 3',
      warningMessage: null,
    })
  })
})
