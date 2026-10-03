import { ArrowUpRight, Briefcase, Search, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useInterfaceLanguage } from '@/i18n/interface-language'
import { AssistantStockPicker, type AssistantStockSearchResult } from './AssistantStockPicker'

interface AssistantWelcomeProps {
  onSubmit: (question: string) => void
  onSelectStock?: (stock: AssistantStockSearchResult) => void
  disabled?: boolean
}

/** First-run surface for the full-page assistant before a conversation exists. */
export function AssistantWelcome({ onSubmit, onSelectStock, disabled = false }: AssistantWelcomeProps) {
  const { t, i18n } = useTranslation('configuration')
  const reportLanguage = useInterfaceLanguage()
  const uiT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const reportT = i18n.getFixedT(reportLanguage, 'configuration') as unknown as (key: string, options?: Record<string, unknown>) => string
  const quickQuestions = [
    { label: uiT('assistantPage.welcome.analyzeStock'), question: reportT('assistantPage.askStock', { market: 'CN', symbol: '600519', name: 'Kweichow Moutai' }), icon: Search, kind: 'stock' },
    { label: uiT('assistantPage.welcome.diagnosePortfolio'), question: reportT('assistantPage.welcome.diagnoseQuestion'), icon: Briefcase, kind: 'question' },
    { label: uiT('assistantPage.welcome.findOpportunity'), question: reportT('assistantPage.welcome.opportunityQuestion'), icon: Sparkles, kind: 'question' },
  ]
  const [question, setQuestion] = useState('')
  const [stockPickerOpen, setStockPickerOpen] = useState(false)

  const submit = (nextQuestion = question) => {
    const content = nextQuestion.trim()
    if (!content || disabled) return
    setQuestion('')
    onSubmit(content)
  }

  const selectStock = (stock: AssistantStockSearchResult) => {
    setStockPickerOpen(false)
    if (onSelectStock) {
      onSelectStock(stock)
      return
    }
    onSubmit(reportT('assistantPage.askStock', { market: stock.market, symbol: stock.symbol, name: stock.name }))
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col items-center justify-start overflow-y-auto px-4 py-8 text-center sm:justify-center sm:px-10 sm:py-12 scrollbar">
      <p className="mb-4 text-[11px] font-semibold tracking-[0.16em] text-primary sm:text-[12px]">
        PANWATCH · {uiT('assistantPage.welcome.brandLabel')}
      </p>
      <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-4xl md:text-5xl">
        {uiT('assistantPage.welcome.title')}
      </h1>
      <p className="mt-4 max-w-2xl text-[14px] leading-6 text-muted-foreground sm:mt-5 sm:text-[15px] sm:leading-7 md:text-[17px]">
        {uiT('assistantPage.welcome.description')}
      </p>

      {stockPickerOpen ? (
        <AssistantStockPicker
          onSelect={selectStock}
          onCancel={() => setStockPickerOpen(false)}
          disabled={disabled}
        />
      ) : (
        <>
          <form
            className="mt-6 flex w-full max-w-3xl items-center gap-2 rounded-2xl border border-border/70 bg-background p-2 shadow-[0_18px_50px_-32px_hsl(var(--foreground)/0.5)] transition-shadow focus-within:ring-2 focus-within:ring-primary/20 sm:mt-9"
            onSubmit={(event) => {
              event.preventDefault()
              submit()
            }}
          >
            <Search className="ml-2 h-5 w-5 shrink-0 text-muted-foreground sm:ml-3" />
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              disabled={disabled}
              className="h-11 min-w-0 flex-1 bg-transparent text-[14px] text-foreground outline-none placeholder:text-muted-foreground/80 sm:h-12 sm:text-[15px]"
              placeholder={uiT('assistantPage.welcome.searchPlaceholder')}
              aria-label={uiT('assistantPage.welcome.startResearch')}
            />
            <button
              type="submit"
              disabled={disabled || !question.trim()}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
              aria-label={uiT('assistantPage.welcome.send')}
            >
              <ArrowUpRight className="h-4 w-4" />
            </button>
          </form>

          <div className="mt-6 flex w-full flex-col justify-center gap-2 sm:mt-7 sm:w-auto sm:flex-row sm:flex-wrap sm:gap-2.5">
            {quickQuestions.map(({ label, question: quickQuestion, icon: Icon, kind }) => (
              <button
                key={label}
                type="button"
                disabled={disabled}
                onClick={() => kind === 'stock' ? setStockPickerOpen(true) : submit(quickQuestion)}
                className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-border/60 bg-card px-4 py-2.5 text-[13px] font-medium text-foreground shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
              >
                <Icon className="h-3.5 w-3.5 text-primary" />
                {label}
              </button>
            ))}
          </div>
        </>
      )}

      <div className="mt-10 grid w-full max-w-3xl gap-3 text-left sm:mt-16 sm:grid-cols-3">
        {[
          ['01', uiT('assistantPage.welcome.symbolPathTitle'), uiT('assistantPage.welcome.symbolPathDescription')],
          ['02', uiT('assistantPage.welcome.portfolioPathTitle'), uiT('assistantPage.welcome.portfolioPathDescription')],
          ['03', uiT('assistantPage.welcome.questionPathTitle'), uiT('assistantPage.welcome.questionPathDescription')],
        ].map(([index, title, description]) => (
          <div key={index} className="rounded-2xl border border-border/60 bg-card/70 p-5">
            <span className="inline-flex rounded-lg bg-primary/10 px-2 py-1 text-[12px] font-semibold text-primary">{index}</span>
            <h2 className="mt-5 text-[15px] font-semibold text-foreground">{title}</h2>
            <p className="mt-2 text-[12px] leading-5 text-muted-foreground">{description}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
