import { useEffect, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { chatApi, type AgentPermissions } from '@panwatch/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@panwatch/base-ui/components/ui/dialog'
import { AgentPermissionsPanel } from '@/components/assistant/AgentPermissionsPanel'
import { AssistantConfigPanel } from '@/components/assistant/AssistantConfigPanel'

interface AssistantPermissionsDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** Inline assistant settings surface for tool permissions and context engineering. */
export function AssistantPermissionsDrawer({ open, onOpenChange }: AssistantPermissionsDrawerProps) {
  const [permissions, setPermissions] = useState<AgentPermissions | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    let active = true
    setError('')
    chatApi.getAgentPermissions()
      .then((next) => {
        if (active) setPermissions(next)
      })
      .catch(() => {
        if (active) setError('无法加载工具权限，请稍后重试。')
      })
    return () => { active = false }
  }, [open])

  const changePermission = async (change: {
    selector_kind: 'tool' | 'risk'
    selector_value: string
    mode: 'allow' | 'ask' | 'deny'
    risk: 'read' | 'write' | 'external' | 'destructive'
  }) => {
    try {
      setError('')
      setPermissions(await chatApi.updateAgentPermission(change))
    } catch {
      setError('保存权限设置失败，请重试。')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="right-0 left-auto top-0 h-dvh w-full max-w-md translate-x-0 translate-y-0 rounded-none border-l border-border/60 p-0 md:top-0 md:translate-y-0">
        <DialogHeader className="border-b border-border/50 px-5 py-5 pr-12">
          <DialogTitle className="flex items-center gap-2">
            <span className="rounded-lg bg-primary/10 p-1.5 text-primary"><SlidersHorizontal className="h-4 w-4" /></span>
            小助手配置
          </DialogTitle>
          <DialogDescription>管理工具权限，以及上下文压缩使用的模型和预算。</DialogDescription>
        </DialogHeader>
        <div className="h-[calc(100dvh-5.75rem)] overflow-y-auto p-4">
          {error && <p className="mb-3 rounded-xl bg-destructive/10 px-3 py-2 text-[12px] text-destructive">{error}</p>}
          {permissions ? (
            <AgentPermissionsPanel permissions={permissions} onChange={(change) => { void changePermission(change) }} variant="drawer" />
          ) : !error ? (
            <div className="flex items-center gap-2 py-10 text-[13px] text-muted-foreground">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-current/30 border-t-current" />
              正在加载工具权限…
            </div>
          ) : null}
          <AssistantConfigPanel />
        </div>
      </DialogContent>
    </Dialog>
  )
}
