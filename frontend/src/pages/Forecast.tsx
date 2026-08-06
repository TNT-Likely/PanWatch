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
  const { toast } = useToast()

  useEffect(() => {
    fetchAPI<{ status: string }>('/api/forecast/health')
      .then(d => setEngineStatus(d.status === 'ok' ? 'ok' : 'down'))
      .catch(() => setEngineStatus('down'))
  }, [])

  const runPredict = async () => {
    if (!/^\d{6}$/.test(symbol)) {
      toast('请输入 6 位股票代码', 'error')
      return
    }
    setLoading(true)
    setResult(null)
    try {
      const d = await fetchAPI<PredictResult>(`/api/forecast/predict?symbol=${symbol}&days=${days}`)
      setResult(d)
    } catch (e: any) {
      toast(e?.message || '预测失败', 'error')
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
                  <span>Kronos（MC{n.result.models.kronos.n_samples}采样）</span>
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
