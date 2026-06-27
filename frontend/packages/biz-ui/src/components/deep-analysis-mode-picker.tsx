import {
  type DeepAnalysisMode,
  deepAnalysisModeDescription,
  deepAnalysisModeLabel,
  saveDeepAnalysisMode,
} from '@panwatch/api/tradingagents'

export function DeepAnalysisModePicker({
  mode,
  onChange,
  className = '',
}: {
  mode: DeepAnalysisMode
  onChange: (mode: DeepAnalysisMode) => void
  className?: string
}) {
  const options: Array<{ value: DeepAnalysisMode; label: string }> = [
    { value: 'full', label: deepAnalysisModeLabel('full') },
    { value: 'fundamentals', label: deepAnalysisModeLabel('fundamentals') },
  ]

  return (
    <div className={`space-y-1.5 ${className}`}>
      <div className="text-[11px] text-muted-foreground">分析范围</div>
      <div className="inline-flex rounded-lg border border-border/60 bg-accent/20 p-0.5">
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            className={`px-2.5 py-1 text-[11px] rounded-md transition-colors ${
              mode === opt.value
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
            onClick={() => {
              onChange(opt.value)
              saveDeepAnalysisMode(opt.value)
            }}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <div className="text-[10px] text-muted-foreground leading-relaxed">
        {deepAnalysisModeDescription(mode)}
      </div>
    </div>
  )
}
