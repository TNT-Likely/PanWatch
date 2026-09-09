import ChatWidget from '@/components/ChatWidget'
import { Bot } from 'lucide-react'

/** Navigation-level home for the PanAgent-powered interactive assistant. */
export default function AssistantPage() {
  return (
    <div className="max-w-6xl mx-auto space-y-4 md:space-y-5">
      <section className="card px-5 py-4 md:px-6 md:py-5 flex items-start gap-3">
        <div className="w-9 h-9 shrink-0 rounded-2xl bg-primary/10 text-primary flex items-center justify-center">
          <Bot className="w-4 h-4" />
        </div>
        <div>
          <h1 className="text-[16px] font-semibold text-foreground">智能助手</h1>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            基于 PanAgent 的通用助手。它会在需要时查询持仓、行情和研究记录，再给出有依据的回答。
          </p>
        </div>
      </section>
      <ChatWidget embedded />
    </div>
  )
}

