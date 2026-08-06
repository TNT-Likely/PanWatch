import { useEffect, useState } from 'react'
import { TrendingUp, LineChart, RefreshCw, Activity } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface KronosResult {
  median: number[]
  p5: number[]
  p95: number[]
  n_samples: number
}

interface PredictResult {
  symbol: string
  last_close: number
  last_date: string
  pred_days: number
  prediction: number[]
  direction: string
  expected_pct: number
  models: {
    kronos: KronosResult
    xgboost: number[] | null
    linreg: number[] | null
  }
  sentiment?: {
    events: { source: string; title?: string; text?: string; date?: string }[]
    market_sentiment?: { limit_up_count: number; top_sectors: { name: string; count: number }[] } | null
    adjustment_pct: number
    notes: string[]
  }
  elapsed_ms: number
}

interface BacktestResult {
  symbol: string
  windows_tested: number
  direction_hits: number
  direction_accuracy_pct: number
  recent_samples: { date: string; pred_close: number; actual_close: number; hit: boolean }[]
}

export default function ForecastPage() {
  const [symbol, setSymbol] = useState('002361')
  const [days, setDays] = useState(5)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<PredictResult | null>(null)
  const [backtest, setBacktest] = useState<BacktestResult | null>(null)
  const [engineStatus, setEngineStatus] = useState<'checking' | 'ok' | 'down'>('checking')
  const [taskStatus, setTaskStatus] = useState('')
  const [taskLogs, setTaskLogs] = useState<string[]>([])
  const { toast } = useToast()

  useEffect(() => {
    // 每 30 秒轮询引擎状态(引擎可能在页面打开后启动)
    let cancelled = false
    const check = () => {
      fetchAPI<{ status: string }>('/api/forecast/health')
        .then(d => {
          if (!cancelled) setEngineStatus(d.status === 'ok' ? 'ok' : 'down')
        })
        .catch(() => {
          if (!cancelled) setEngineStatus('down')
        })
    }
    check()
    const timer = setInterval(check, 30000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const runPredict = async () => {
    if (!/^\d{6}$/.test(symbol)) {
      toast('请输入 6 位股票代码', 'error')
      return
    }
    setLoading(true)
    setResult(null)
    setTaskLogs([])
    setTaskStatus('running')
    const tid = `task_${Date.now()}`
    try {
      // 并行: 启动预测 + 轮询进度
      const predictPromise = fetchAPI<PredictResult>(`/api/forecast/predict?symbol=${symbol}&days=${days}&task_id=${tid}`)
      let pollDone = false
      const pollPromise = (async () => {
        while (!pollDone) {
          await new Promise(res => setTimeout(res, 1500))
          try {
            const s = await fetchAPI<any>(`/api/forecast/predict/status?task_id=${tid}`)
            if (s?.logs) setTaskLogs([...s.logs])
            if (s?.status === 'done' || s?.status === 'error' || s?.status === 'not_found') {
              setTaskStatus(s.status)
              break
            }
          } catch { /* 忽略轮询错误 */ }
        }
      })()
      const d = await predictPromise
      pollDone = true
      setResult(d)
      setTaskStatus('done')
    } catch (e: any) {
      toast(e?.message || '预测失败(请检查股票代码是否正确)', 'error')
      setTaskStatus('error')
    } finally {
      setLoading(false)
    }
  }

  const runBacktest = async () => {
    if (!/^\d{6}$/.test(symbol)) {
      toast('请输入 6 位股票代码', 'error')
      return
    }
    setLoading(true)
    try {
      const d = await fetchAPI<BacktestResult>(`/api/forecast/backtest?symbol=${symbol}`)
      setBacktest(d)
    } catch (e: any) {
      toast(e?.message || '回测失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  const dirColor = (dir: string) =>
    dir === 'up' ? 'text-red-500' : dir === 'down' ? 'text-green-500' : 'text-gray-500'

  return (
    <div className="space-y-6 p-4 md:p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <TrendingUp className="h-6 w-6" /> 预测回测
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Kronos + XGBoost + 线性回归 多模型投票预测（数据源：baostock 不复权）
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`h-2.5 w-2.5 rounded-full ${engineStatus === 'ok' ? 'bg-green-500' : engineStatus === 'down' ? 'bg-red-500' : 'bg-yellow-400'}`} />
          <span className="text-muted-foreground">
            预测引擎 {engineStatus === 'ok' ? '运行中' : engineStatus === 'down' ? '未启动' : '检测中'}
          </span>
        </div>
      </div>

      {/* 输入区 */}
      <div className="card p-4">
        <div className="mb-3">
          <div className="text-lg font-bold">发起预测</div>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1.5">
            <Label>股票代码</Label>
            <Input
              value={symbol}
              onChange={e => setSymbol(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="如 002361"
              className="w-32"
            />
          </div>
          <div className="space-y-1.5">
            <Label>预测天数</Label>
            <Input
              type="number"
              min={1}
              max={20}
              value={days}
              onChange={e => setDays(Number(e.target.value) || 5)}
              className="w-24"
            />
          </div>
          <Button onClick={runPredict} disabled={loading}>
            <Activity className="mr-2 h-4 w-4" /> {loading ? '预测中(约30-60s)...' : '开始预测'}
          </Button>
          <Button variant="outline" onClick={runBacktest} disabled={loading}>
            <LineChart className="mr-2 h-4 w-4" /> 历史回测
          </Button>
        </div>
      </div>

      {/* 预测进度日志 */}
      {(loading || taskLogs.length > 0) && (
        <div className="card p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className={`h-2.5 w-2.5 rounded-full ${taskStatus === 'done' ? 'bg-green-500' : taskStatus === 'error' ? 'bg-red-500' : 'bg-blue-500 animate-pulse'}`} />
            <span className="font-medium">
              {taskStatus === 'done' ? '预测完成' : taskStatus === 'error' ? '预测失败' : '预测进行中...'}
            </span>
            {loading && <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />}
          </div>
          <div className="bg-muted/50 rounded p-3 font-mono text-xs space-y-1 max-h-48 overflow-y-auto">
            {taskLogs.length === 0 ? (
              <div className="text-muted-foreground">正在连接预测引擎...</div>
            ) : (
              taskLogs.map((log, i) => (
                <div key={i} className="text-muted-foreground">{log}</div>
              ))
            )}
          </div>
        </div>
      )}

      {/* 预测结果 */}
      {result && (
        <div className="card p-4">
          <div className="mb-3">
            <div className="flex items-center justify-between">
              <span>预测结果：{result.symbol}</span>
              <span className={`text-lg font-bold ${dirColor(result.direction)}`}>
                {result.direction === 'up' ? '↑ 看多' : result.direction === 'down' ? '↓ 看空' : '→ 横盘'}
                {' '}({result.expected_pct > 0 ? '+' : ''}{result.expected_pct}%)
              </span>
            </div>
          </div>
          <div className="space-y-4">
            <div className="text-sm text-muted-foreground">
              基准价 {result.last_close}（{result.last_date}）→ 预测 {result.pred_days} 天，耗时 {result.elapsed_ms}ms
            </div>

            {/* 预测价格序列 */}
            <div>
              <div className="text-sm font-medium mb-2">预测价格（综合投票）</div>
              <div className="flex flex-wrap gap-2">
                {result.prediction.map((p, i) => (
                  <div key={i} className="bg-muted rounded-lg px-3 py-2 text-center">
                    <div className="text-xs text-muted-foreground">T+{i + 1}</div>
                    <div className={`font-mono font-bold ${p > result.last_close ? 'text-red-500' : 'text-green-500'}`}>
                      {p.toFixed(2)}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {((p / result.last_close - 1) * 100).toFixed(1)}%
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* 三模型对比 */}
            <div>
              <div className="text-sm font-medium mb-2">三模型对比</div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between bg-muted/50 rounded px-3 py-2">
                  <span>Kronos（MC{result.models.kronos.n_samples}采样）</span>
                  <span className="font-mono">
                    {result.models.kronos.median[0].toFixed(2)} → {result.models.kronos.median[result.models.kronos.median.length - 1].toFixed(2)}
                    <span className="text-muted-foreground ml-2">
                      P5 {result.models.kronos.p5[0].toFixed(2)} ~ P95 {result.models.kronos.p95[0].toFixed(2)}
                    </span>
                  </span>
                </div>
                <div className="flex justify-between bg-muted/50 rounded px-3 py-2">
                  <span>XGBoost</span>
                  <span className="font-mono">
                    {result.models.xgboost ? `${result.models.xgboost[0].toFixed(2)} → ${result.models.xgboost[result.models.xgboost.length - 1].toFixed(2)}` : '不可用'}
                  </span>
                </div>
                <div className="flex justify-between bg-muted/50 rounded px-3 py-2">
                  <span>线性回归</span>
                  <span className="font-mono">
                    {result.models.linreg ? `${result.models.linreg[0].toFixed(2)} → ${result.models.linreg[result.models.linreg.length - 1].toFixed(2)}` : '不可用'}
                  </span>
                </div>
              </div>
            </div>

            {/* 消息情绪面 */}
            {result.sentiment && (
              <div>
                <div className="text-sm font-medium mb-2">消息情绪面</div>
                <div className={`rounded px-3 py-2 text-sm mb-2 ${result.sentiment.adjustment_pct >= 0 ? 'bg-red-500/10 text-red-500' : 'bg-green-500/10 text-green-500'}`}>
                  情绪修正系数：{result.sentiment.adjustment_pct > 0 ? '+' : ''}{result.sentiment.adjustment_pct}%
                  {result.sentiment.notes?.length > 0 && (
                    <span className="text-muted-foreground ml-2 text-xs">
                      ({result.sentiment.notes.join('；')})
                    </span>
                  )}
                </div>
                {result.sentiment.market_sentiment && (
                  <div className="text-xs text-muted-foreground mb-2">
                    今日涨停 {result.sentiment.market_sentiment.limit_up_count} 家，
                    板块分布：{result.sentiment.market_sentiment.top_sectors?.map(s => `${s.name}×${s.count}`).join('、') || '无'}
                  </div>
                )}
                {result.sentiment.events?.length > 0 && (
                  <div className="text-xs space-y-1">
                    {result.sentiment.events.slice(0, 5).map((e, i) => (
                      <div key={i} className="flex gap-2">
                        <span className="text-muted-foreground shrink-0">[{e.source}]</span>
                        <span className="truncate">{e.title || String(e.text || '').slice(0, 60)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 回测结果 */}
      {backtest && (
        <div className="card p-4">
          <div className="mb-3">
            <div className="text-lg font-bold">回测结果：{backtest.symbol}</div>
          </div>
          <div className="space-y-4">
            <div className="flex gap-6">
              <div>
                <div className="text-3xl font-bold">{backtest.direction_accuracy_pct}%</div>
                <div className="text-xs text-muted-foreground">方向命中率</div>
              </div>
              <div>
                <div className="text-3xl font-bold">{backtest.direction_hits}/{backtest.windows_tested}</div>
                <div className="text-xs text-muted-foreground">命中/测试窗口</div>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              注：回测用线性回归快速预测方向，作为模型参考；Kronos 全量 MC 回测较慢，仅按需运行。
            </div>
            {backtest.recent_samples.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2">日期</th>
                      <th className="text-right">预测</th>
                      <th className="text-right">实际</th>
                      <th className="text-right">方向</th>
                    </tr>
                  </thead>
                  <tbody>
                    {backtest.recent_samples.slice().reverse().map((s, i) => (
                      <tr key={i} className="border-b">
                        <td className="py-1.5">{s.date}</td>
                        <td className="text-right font-mono">{s.pred_close.toFixed(2)}</td>
                        <td className="text-right font-mono">{s.actual_close.toFixed(2)}</td>
                        <td className={`text-right ${s.hit ? 'text-green-500' : 'text-red-500'}`}>
                          {s.hit ? '✓' : '✗'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="text-xs text-muted-foreground">
        免责声明：预测结果基于历史数据统计模型，不构成投资建议。模型存在偏差，请结合基本面/情绪面综合判断。
      </div>
    </div>
  )
}
