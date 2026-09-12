import { MessageSquareText, Plus, Trash2 } from 'lucide-react'
import type { ChatConversation } from '@panwatch/api'

interface AssistantSidebarProps {
  conversations: ChatConversation[]
  activeConversationId: number | null
  onOpen: (conversation: ChatConversation) => void
  onCreate: () => void
  onDelete: (conversationId: number) => void
}

/** Desktop history rail; its callbacks keep transport state in ChatWidget. */
export function AssistantSidebar({
  conversations,
  activeConversationId,
  onOpen,
  onCreate,
  onDelete,
}: AssistantSidebarProps) {
  return (
    <aside className="flex h-full min-h-0 w-full flex-col border-r border-border/50 bg-card/40 px-3 py-4 backdrop-blur-sm">
      <button
        type="button"
        onClick={onCreate}
        className="flex h-10 items-center justify-center gap-2 rounded-xl border border-primary/25 bg-primary/10 px-3 text-[13px] font-medium text-primary transition-colors hover:bg-primary/15"
      >
        <Plus className="h-4 w-4" />
        新研究
      </button>
      <div className="mt-6 flex items-center gap-2 px-2 text-[11px] font-medium text-muted-foreground">
        <MessageSquareText className="h-3.5 w-3.5" />
        历史会话
      </div>
      <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto pr-1 scrollbar">
        {conversations.length === 0 ? (
          <p className="px-2 py-4 text-[12px] leading-5 text-muted-foreground">你的研究记录会显示在这里。</p>
        ) : conversations.map((conversation) => {
          const title = conversation.title || '新研究'
          const active = conversation.id === activeConversationId
          return (
            <div
              key={conversation.id}
              className={`group flex items-center gap-1 rounded-xl p-1 transition-colors ${
                active ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
              }`}
            >
              <button
                type="button"
                onClick={() => onOpen(conversation)}
                className="min-w-0 flex-1 rounded-lg px-2 py-2 text-left"
                aria-label={title}
              >
                <p className="truncate text-[12px] font-medium">{title}</p>
                <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                  {conversation.stock_symbol ? `${conversation.stock_market}:${conversation.stock_symbol} · ` : ''}
                  {new Date(conversation.created_at).toLocaleDateString()}
                </p>
              </button>
              <button
                type="button"
                onClick={() => onDelete(conversation.id)}
                className="mr-1 rounded-md p-1.5 text-muted-foreground/50 opacity-0 transition-all hover:bg-rose-500/10 hover:text-rose-500 group-hover:opacity-100 focus:opacity-100"
                aria-label={`删除 ${title}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
