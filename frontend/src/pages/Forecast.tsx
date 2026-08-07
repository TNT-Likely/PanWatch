import { useEffect, useState } from 'react'
import { TrendingUp, LineChart, RefreshCw, Activity, Download, History } from 'lucide-react'
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
  stock_name?: string
  last_close: number
  last_date: string
  pred_days: number
  prediction: number[]
  direction: string
  expected_pct: number
  models: {
    kronos: KronosResult
    lag_llama?: { median: number[]; p10: number[]; p90: number[]; n_samples: number } | null
    xgboost: number[] | null
    linreg: number[] | null
  }
  sentiment?: {
    events: { source: string; title?: string; text?: string; date?: string }[]
    market_sentiment?: { limit_up_count: number; top_sectors: { name: string; count: number }[] } | null
    adjustment_pct: number
    notes: string[]
  }
  recommendation?: {
    action: string
    tone: string
    confidence: string
    risk_note?: string
    target_price: number
    expected_pct: number
    stop_loss: number
    summary: string
  }
  elapsed_ms: number
}

interface ForecastHistoryItem {
  id: number
  symbol: string
  stock_name?: string
  last_close: number
  last_date: string
  target_date?: string
  pred_days: number
  direction: string
  expected_pct: number
  prediction: number[]
  action: string
  confidence: string
  target_price: number
  stop_loss: number
  summary: string
  sentiment_adj: number
  created_at: string
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
  const [days] = useState(5)
  const [searchText, setSearchText] = useState('神剑股份')
  const [searchResults, setSearchResults] = useState<{ symbol: string; name: string }[]>([])
  const [stockName, setStockName] = useState('')
  const [targetDate, setTargetDate] = useState('')
  const [lastKlineDate, setLastKlineDate] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<PredictResult | null>(null)
  const [backtest, setBacktest] = useState<BacktestResult | null>(null)
  const [engineStatus, setEngineStatus] = useState<'checking' | 'ok' | 'down'>('checking')
  const [taskStatus, setTaskStatus] = useState('')
  const [taskLogs, setTaskLogs] = useState<string[]>([])
  const [history, setHistory] = useState<ForecastHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [detail, setDetail] = useState<ForecastHistoryItem | null>(null)
  const { toast } = useToast()

  // 股票搜索(输入名称/代码) — 用 PanWatch 自带 /stocks/search(返回 list)
  const doSearch = async (q: string) => {
    if (!q.trim()) return
    try {
      const d = await fetchAPI<{ symbol: string; name: string }[] | { items: { symbol: string; name: string }[] }>(
        `/stocks/search?q=${encodeURIComponent(q)}`
      )
      // PanWatch 返回 list;兼容 {items} 结构;过滤 A 股主板(6/0 开头,排除 300/688/8xx/港股)
      const list = (Array.isArray(d) ? d : (d as any)?.items || []).filter((s: any) => {
        const sym = s.symbol || ''
        if (!/^\d{6}$/.test(sym)) return false
        return sym.startsWith('60') || sym.startsWith('000') || sym.startsWith('002')
      })
      setSearchResults(list)
    } catch {
      setSearchResults([])
    }
  }

  // 输入防抖搜索
  const handleSearchInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value
    setSearchText(v)
    setStockName('')
    // 纯数字6位 = 直接当代码
    if (/^\d{6}$/.test(v)) {
      setSymbol(v)
      setSearchResults([])
      return
    }
    if (v.length >= 2) {
      setTimeout(() => doSearch(v), 300)
    } else {
      setSearchResults([])
    }
  }

  // 选中搜索结果
  const selectStock = (s: { symbol: string; name: string }) => {
    setSymbol(s.symbol)
    setStockName(s.name)
    setSearchText(`${s.name} ${s.symbol}`)
    setSearchResults([])
  }

  // 加载历史预测列表
  const loadHistory = async () => {
    setHistoryLoading(true)
    try {
      const d = await fetchAPI<{ items: ForecastHistoryItem[] }>(`/forecast/history?limit=30`)
      setHistory(d?.items || [])
    } catch {
      setHistory([])
    } finally {
      setHistoryLoading(false)
    }
  }

  useEffect(() => { loadHistory() }, [])

  // 检测引擎状态(可手动刷新调用)
  const checkEngine = () => {
    setEngineStatus('checking')
    fetchAPI<{ status: string }>('/forecast/health')
      .then(d => {
        // 引擎可能返回 {status: "unreachable"} (代理活着但引擎没起)
        setEngineStatus(d?.status === 'ok' ? 'ok' : 'down')
      })
      .catch(() => setEngineStatus('down'))
  }

  useEffect(() => {
    // 每 30 秒轮询引擎状态(引擎可能在页面打开后启动)
    let cancelled = false
    const timer = setInterval(() => {
      if (!cancelled) checkEngine()
    }, 30000)
    checkEngine()
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const refreshEngine = () => checkEngine()

  // 下载预测卡片图片
  const downloadCard = async () => {
    try {
      const res = await fetch(`/api/forecast/card?symbol=${symbol}`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
      })
      if (!res.ok) throw new Error('卡片生成失败')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `forecast_${symbol}_${new Date().toISOString().slice(0, 10)}.png`
      a.click()
      URL.revokeObjectURL(url)
      toast('卡片已下载', 'success')
    } catch (e: any) {
      toast(e?.message || '卡片下载失败', 'error')
    }
  }

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
      // ⚠️ predict 需要长超时(Kronos MC30 推理 20-30s,默认 20s 会 abort)
      const targetParam = targetDate ? `&target_date=${targetDate}` : ''
      const predictPromise = fetchAPI<PredictResult>(
        `/forecast/predict?symbol=${symbol}&days=${days}&task_id=${tid}${targetParam}`,
        { timeoutMs: 300000 }
      )
      let pollDone = false
      const pollPromise = (async () => {
        while (!pollDone) {
          await new Promise(res => setTimeout(res, 1500))
          try {
            const s = await fetchAPI<any>(`/forecast/predict/status?task_id=${tid}`)
            if (s?.logs) setTaskLogs([...s.logs])
            if (s?.status === 'done') {
              setTaskStatus('done')
              setResult(s.result)
              pollDone = true
              break
            }
            if (s?.status === 'error' || s?.status === 'not_found') {
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
      loadHistory()
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
      const d = await fetchAPI<BacktestResult>(`/forecast/backtest?symbol=${symbol}`)
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
            Kronos + Lag-Llama + XGBoost + 线性回归 四模型投票预测（数据源：baostock 不复权）
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`h-2.5 w-2.5 rounded-full ${engineStatus === 'ok' ? 'bg-green-500' : engineStatus === 'down' ? 'bg-red-500' : 'bg-yellow-400'}`} />
          <span className="text-muted-foreground">
            预测引擎 {engineStatus === 'ok' ? '运行中' : engineStatus === 'down' ? '未启动' : '检测中'}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2"
            onClick={refreshEngine}
            disabled={engineStatus === 'checking'}
            title="重新检测引擎状态"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${engineStatus === 'checking' ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* 输入区 */}
      <div className="card p-4">
        <div className="mb-3">
          <div className="text-lg font-bold">发起预测</div>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1.5">
            <Label>股票</Label>
            <div className="relative">
              <Input
                value={searchText}
                onChange={handleSearchInput}
                onFocus={() => searchText && doSearch(searchText)}
                placeholder="输入名称或代码,如 神剑/002361"
                className="w-48"
              />
              {searchResults.length > 0 && (
                <div className="absolute z-20 mt-1 w-56 max-h-56 overflow-y-auto bg-popover border rounded-lg shadow-lg">
                  {searchResults.map((s, i) => (
                    <button
                      key={i}
                      type="button"
                      className="w-full text-left px-3 py-2 hover:bg-muted/50 text-sm flex justify-between"
                      onClick={() => selectStock(s)}
                    >
                      <span>{s.name}</span>
                      <span className="font-mono text-muted-foreground">{s.symbol}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {stockName && (
              <div className="text-xs text-muted-foreground mt-1">已选：{symbol} {stockName}</div>
            )}
          </div>
          <div className="space-y-1.5">
            <Label>预测至</Label>
            <Input
              type="date"
              value={targetDate}
              min={lastKlineDate ? lastKlineDate : undefined}
              onChange={e => setTargetDate(e.target.value)}
              className="w-36"
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
              <span>预测结果：{result.symbol}{result.stock_name ? ` ${result.stock_name}` : ''}</span>
              <span className={`text-lg font-bold ${dirColor(result.direction)}`}>
                {result.direction === 'up' ? '↑ 看多' : result.direction === 'down' ? '↓ 看空' : '→ 横盘'}
                {' '}({result.expected_pct > 0 ? '+' : ''}{result.expected_pct}%)
              </span>
              <span className="flex items-center gap-2">
                <Button variant="outline" size="sm" className="h-8" onClick={downloadCard}>
                  <Download className="mr-1 h-3.5 w-3.5" /> 下载卡片
                </Button>
              </span>
            </div>
          </div>
          <div className="space-y-4">
            <div className="text-sm text-muted-foreground">
              基准价 {result.last_close}（{result.last_date}）→ 预测 {result.pred_days} 天，耗时 {result.elapsed_ms}ms
              {(() => {
                const today = new Date().toISOString().slice(0, 10);
                if (result.last_date < today) {
                  return (
                    <span className="ml-2 text-yellow-500/80" title={`baostock 收盘后才入库当日数据;${result.last_date} 是最新可用历史,基准价已滞后 1 个交易日`}>
                      ⚠️ 基准日滞后今日 ({today})
                    </span>
                  );
                }
                return null;
              })()}
            </div>

            {/* 操作建议 */}
            {result.recommendation && (
              <div className={`rounded-lg border px-4 py-3 ${result.direction === 'up' ? 'border-red-500/30 bg-red-500/5' : result.direction === 'down' ? 'border-green-500/30 bg-green-500/5' : 'border-gray-500/30 bg-gray-500/5'}`}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-bold text-lg">操作建议：{result.recommendation.action}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${result.recommendation.confidence === '高' ? 'bg-green-500/20 text-green-500' : result.recommendation.confidence === '中' ? 'bg-yellow-500/20 text-yellow-500' : 'bg-red-500/20 text-red-500'}`}>
                    置信度{result.recommendation.confidence}
                  </span>
                </div>
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
                  <span>目标价：<span className="font-mono font-bold text-foreground">{result.recommendation.target_price}</span></span>
                  <span>止损参考：<span className="font-mono font-bold text-foreground">{result.recommendation.stop_loss}</span></span>
                  <span>预期：<span className={`font-mono font-bold ${result.expected_pct >= 0 ? 'text-red-500' : 'text-green-500'}`}>{result.expected_pct > 0 ? '+' : ''}{result.expected_pct}%</span></span>
                  {result.recommendation.risk_note && <span className="text-amber-500">{result.recommendation.risk_note}</span>}
                </div>
              </div>
            )}

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

            {/* 四模型对比 */}
            <div>
              <div className="text-sm font-medium mb-2">四模型对比</div>
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
                  <span>Lag-Llama<span className="text-muted-foreground text-xs ml-1">(短周期可靠)</span></span>
                  <span className="font-mono">
                    {result.models.lag_llama ? `${result.models.lag_llama.median[0].toFixed(2)} → ${result.models.lag_llama.median[result.models.lag_llama.median.length - 1].toFixed(2)}` : '不可用'}
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

      {/* 历史预测列表 */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4" />
            <span className="text-lg font-bold">历史预测</span>
          </div>
          <Button variant="ghost" size="sm" className="h-8" onClick={loadHistory} disabled={historyLoading}>
            <RefreshCw className={`h-3.5 w-3.5 ${historyLoading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
        {history.length === 0 ? (
          <div className="text-sm text-muted-foreground py-4 text-center">
            暂无预测记录，先发起一次预测
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="text-left py-2">代码</th>
                  <th className="text-left">基准日</th>
                  <th className="text-left">目标日</th>
                  <th className="text-right">方向</th>
                  <th className="text-right">预期</th>
                  <th className="text-right">目标价</th>
                  <th className="text-right">止损</th>
                  <th className="text-left">操作建议</th>
                  <th className="text-left">置信</th>
                </tr>
              </thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.id} className="border-b hover:bg-muted/30 cursor-pointer" onClick={() => setDetail(h)}>
                    <td className="py-2 font-mono">{h.symbol}{h.stock_name ? ` ${h.stock_name}` : ''}</td>
                    <td>{h.last_date}</td>
                    <td className="font-mono">{h.target_date || '-'}</td>
                    <td className={`text-right font-bold ${h.direction === 'up' ? 'text-red-500' : h.direction === 'down' ? 'text-green-500' : ''}`}>
                      {h.direction === 'up' ? '↑' : h.direction === 'down' ? '↓' : '→'}
                    </td>
                    <td className={`text-right font-mono ${h.expected_pct >= 0 ? 'text-red-500' : 'text-green-500'}`}>
                      {h.expected_pct > 0 ? '+' : ''}{h.expected_pct}%
                    </td>
                    <td className="text-right font-mono">{h.target_price ?? '-'}</td>
                    <td className="text-right font-mono">{h.stop_loss ?? '-'}</td>
                    <td>{h.action || '-'}</td>
                    <td>{h.confidence || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="text-xs text-muted-foreground">
        免责声明：预测结果基于历史数据统计模型，不构成投资建议。模型存在偏差，请结合基本面/情绪面综合判断。
      </div>

      {/* 历史预测详情弹窗 */}
      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setDetail(null)}>
          <div className="bg-background border rounded-lg shadow-xl max-w-lg w-full max-h-[80vh] overflow-y-auto p-5" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <div className="text-lg font-bold">
                {detail.symbol} {detail.stock_name || ''}
              </div>
              <button className="text-muted-foreground hover:text-foreground" onClick={() => setDetail(null)}>✕</button>
            </div>

            <div className="space-y-3 text-sm">
              <div className={`rounded-lg border px-3 py-2 ${detail.direction === 'up' ? 'border-red-500/30 bg-red-500/5' : detail.direction === 'down' ? 'border-green-500/30 bg-green-500/5' : ''}`}>
                <div className="font-bold">
                  {detail.direction === 'up' ? '↑ 看多' : detail.direction === 'down' ? '↓ 看空' : '→ 横盘'}
                  {' '}{detail.expected_pct > 0 ? '+' : ''}{detail.expected_pct}%
                </div>
                <div className="text-muted-foreground text-xs mt-1">
                  操作建议：{detail.action || '-'} | 置信度：{detail.confidence || '-'}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="bg-muted/40 rounded px-2 py-1.5">
                  <div className="text-muted-foreground">基准日</div>
                  <div className="font-mono font-bold">{detail.last_date}</div>
                </div>
                <div className="bg-muted/40 rounded px-2 py-1.5">
                  <div className="text-muted-foreground">目标日</div>
                  <div className="font-mono font-bold">{detail.target_date || '-'}</div>
                </div>
                <div className="bg-muted/40 rounded px-2 py-1.5">
                  <div className="text-muted-foreground">基准价</div>
                  <div className="font-mono font-bold">{detail.last_close}</div>
                </div>
                <div className="bg-muted/40 rounded px-2 py-1.5">
                  <div className="text-muted-foreground">目标价</div>
                  <div className="font-mono font-bold">{detail.target_price ?? '-'}</div>
                </div>
                <div className="bg-muted/40 rounded px-2 py-1.5">
                  <div className="text-muted-foreground">止损参考</div>
                  <div className="font-mono font-bold">{detail.stop_loss ?? '-'}</div>
                </div>
                <div className="bg-muted/40 rounded px-2 py-1.5">
                  <div className="text-muted-foreground">情绪修正</div>
                  <div className={`font-mono font-bold ${(detail.sentiment_adj || 0) >= 0 ? 'text-red-500' : 'text-green-500'}`}>
                    {(detail.sentiment_adj || 0) > 0 ? '+' : ''}{detail.sentiment_adj || 0}%
                  </div>
                </div>
              </div>

              {detail.prediction && detail.prediction.length > 0 && (
                <div>
                  <div className="text-muted-foreground text-xs mb-1">预测序列（T+1 起）</div>
                  <div className="flex flex-wrap gap-1.5">
                    {detail.prediction.map((p, i) => (
                      <div key={i} className="bg-muted/40 rounded px-2 py-1 text-center">
                        <div className="text-[10px] text-muted-foreground">T+{i + 1}</div>
                        <div className={`font-mono font-bold text-xs ${p >= detail.last_close ? 'text-red-500' : 'text-green-500'}`}>{p}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {detail.summary && (
                <div className="text-xs text-muted-foreground border-t pt-2">
                  {detail.summary}
                </div>
              )}

              <div className="text-[10px] text-muted-foreground">
                预测时间：{detail.created_at}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
