export type AssistantRecovery = 'configuration' | 'permissions' | 'context' | 'revise' | 'retry'

export function assistantRecovery(code: string): AssistantRecovery {
  if (code === 'ai_context_limit_exceeded') return 'context'
  if (code === 'permission_denied') return 'permissions'
  if (['ai_content_rejected', 'required_tool_call_missing', 'repeated_tool_call', 'tool_call_limit', 'step_limit'].includes(code)) return 'revise'
  if (['ai_quota_exhausted', 'ai_authentication_failed', 'ai_permission_denied', 'ai_model_unavailable', 'ai_request_invalid', 'ai_connection_failed', 'transport_setup_failed', 'model_failed'].includes(code)) return 'configuration'
  return 'retry'
}
