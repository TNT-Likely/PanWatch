import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AssistantConfigPanel } from '@/components/assistant/AssistantConfigPanel'
import { chatApi } from '@panwatch/api'

const config = {
  compression_model_id: 6,
  compression_temperature: 0.1,
  summary_max_tokens: 800,
  max_tokens: 12000,
  soft_limit_tokens: 8400,
  hard_limit_tokens: 10200,
  keep_recent_messages: 8,
  models: [
    { id: 6, name: 'DeepSeek V4 Flash', model: 'deepseek-ai/DeepSeek-V4-Flash', service_name: '硅基流动' },
  ],
}

describe('AssistantConfigPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('loads and saves the context compression model and budget', async () => {
    vi.spyOn(chatApi, 'getAssistantConfig').mockResolvedValue(config)
    const update = vi.spyOn(chatApi, 'updateAssistantConfig').mockResolvedValue({ ...config, max_tokens: 16000 })

    render(<AssistantConfigPanel />)

    expect((await screen.findByLabelText('上下文压缩模型') as HTMLSelectElement).value).toBe('6')
    fireEvent.change(screen.getByLabelText('最大上下文 Token'), { target: { value: '16000' } })
    fireEvent.click(screen.getByRole('button', { name: '保存上下文配置' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith({
      compression_model_id: 6,
      compression_temperature: 0.1,
      summary_max_tokens: 800,
      max_tokens: 16000,
      soft_limit_tokens: 8400,
      hard_limit_tokens: 10200,
      keep_recent_messages: 8,
    }))
  })
})
