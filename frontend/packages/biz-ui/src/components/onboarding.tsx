import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Bot, Bell, CheckCircle2, ChevronRight, Sparkles } from 'lucide-react'
import { Dialog, DialogContent } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface OnboardingProps {
  open: boolean
  onComplete: () => void
  hasStocks: boolean
}

type Step = 'welcome' | 'ai' | 'notify' | 'complete'

export function Onboarding({ open, onComplete, hasStocks }: OnboardingProps) {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('welcome')

  const handleNext = () => {
    if (step === 'welcome') {
      setStep('ai')
    } else if (step === 'ai') {
      setStep('notify')
    } else if (step === 'notify') {
      setStep('complete')
    } else {
      onComplete()
    }
  }

  const handleSkip = () => {
    onComplete()
  }

  const handleGoToSettings = () => {
    onComplete()
    navigate('/settings')
  }

  const handleGoToPortfolio = () => {
    onComplete()
    navigate('/portfolio')
  }

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onComplete()}>
      <DialogContent className="max-w-md p-0 overflow-hidden">
        {/* Progress Indicator */}
        <div className="flex items-center gap-1.5 px-6 pt-6">
          {(['welcome', 'ai', 'notify', 'complete'] as Step[]).map((s, i) => (
            <div
              key={s}
              className={`flex-1 h-1 rounded-full transition-colors ${
                i <= ['welcome', 'ai', 'notify', 'complete'].indexOf(step)
                  ? 'bg-primary'
                  : 'bg-accent/50'
              }`}
            />
          ))}
        </div>

        <div className="p-6 pt-4">
          {step === 'welcome' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <TrendingUp className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                欢迎使用盯盘侠
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                {hasStocks
                  ? '你的自选股已就绪，可以开始使用了'
                  : '我们已为你添加了 5 只热门股票作为示例，你可以立即查看实时行情'
                }
              </p>

              <div className="space-y-3 text-left mb-6">
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center flex-shrink-0">
                    <TrendingUp className="w-4 h-4 text-blue-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">实时行情监控</p>
                    <p className="text-[12px] text-muted-foreground">跟踪自选股价格变动，快速发现异动</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                    <Bot className="w-4 h-4 text-primary" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">AI 智能分析</p>
                    <p className="text-[12px] text-muted-foreground">盘后日报、异动建议、技术分析</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0">
                    <Bell className="w-4 h-4 text-amber-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">智能通知推送</p>
                    <p className="text-[12px] text-muted-foreground">Telegram、企业微信等多渠道推送</p>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Button className="flex-1" onClick={handleNext}>
                  开始使用 <ChevronRight className="w-4 h-4" />
                </Button>
              </div>
              <button
                onClick={handleSkip}
                className="mt-3 text-[12px] text-muted-foreground hover:text-foreground transition-colors"
              >
                跳过引导
              </button>
            </div>
          )}

          {step === 'ai' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <Bot className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                配置 AI 分析
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                连接 AI 服务后，可获得智能分析功能
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">盘后日报自动分析</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">异动 AI 建议</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">技术图表分析</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                支持 OpenAI、智谱、DeepSeek 等服务商
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  稍后再说
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  前往配置
                </Button>
              </div>
            </div>
          )}

          {step === 'notify' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-amber-500 flex items-center justify-center mx-auto mb-4">
                <Bell className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                配置通知渠道
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                配置后可收到实时推送通知
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">盘中异动提醒</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">AI 分析报告推送</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">止盈止损预警</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                支持 Telegram、企业微信等渠道
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  稍后再说
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  前往配置
                </Button>
              </div>
            </div>
          )}

          {step === 'complete' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-emerald-500 flex items-center justify-center mx-auto mb-4">
                <CheckCircle2 className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                设置完成
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                你可以随时在「设置」页面修改配置
              </p>

              <div className="space-y-3">
                <Button className="w-full" onClick={() => onComplete()}>
                  进入 Dashboard
                </Button>
                <Button variant="secondary" className="w-full" onClick={handleGoToPortfolio}>
                  管理自选股
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
