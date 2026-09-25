import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { getBacktestJob, getBacktestSummary, runBacktest, type BacktestRunResult } from '../api/mockApi'

function readNumber(value: string | undefined) {
  const cleaned = Number.parseFloat((value ?? '0').replace(/[^\d.-]/g, ''))
  return Number.isFinite(cleaned) ? cleaned : 0
}

export function BacktestAnalyticsPage() {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['backtests'], queryFn: getBacktestSummary })
  const runMutation = useMutation({ mutationFn: runBacktest })
  const [pair, setPair] = useState('EUR/USD')
  const [timeframe, setTimeframe] = useState('M5')
  const [historyMode, setHistoryMode] = useState<'candles' | 'days'>('candles')
  const [historyValue, setHistoryValue] = useState(250)
  const [lastRunMessage, setLastRunMessage] = useState<string | null>(null)
  const [lastRunResult, setLastRunResult] = useState<BacktestRunResult | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState({ history: 0, backtest: 0, phase: 'idle' })

  const totalTrades = data?.reduce((sum, run) => sum + run.trades, 0) ?? 0
  const totalReturn = data?.reduce((sum, run) => sum + readNumber(run.result), 0) ?? 0
  const maxDrawdown = data?.reduce((max, run) => Math.max(max, readNumber(run.drawdown)), 0) ?? 0

  const summary = [
    { label: 'Net profit', value: data?.[0]?.netProfit ?? '+$0.00' },
    { label: 'Strategy return', value: `${totalReturn.toFixed(2)}%` },
    { label: 'Max DD', value: `${maxDrawdown.toFixed(2)}%` },
    { label: 'Trades', value: String(totalTrades) },
  ]

  const handleRunBacktest = () => {
    setLastRunMessage(null)
    setLastRunResult(null)
    setJobId(null)
    setProgress({ history: 0, backtest: 0, phase: 'queued' })
    runMutation.mutate(
      { pair, timeframe, steps: historyMode === 'candles' ? historyValue : 250, historyMode, historyValue },
      {
        onSuccess: (result) => {
          setLastRunResult(result)
          setJobId(result.id)
          setProgress({ history: result.historyProgress ?? 0, backtest: result.backtestProgress ?? 0, phase: result.status })
          setLastRunMessage(result.message ?? `Backtest queued for ${pair} on ${timeframe}.`)
        },
        onError: () => setLastRunMessage('Backtest request was rejected by the backend safety gate.'),
      },
    )
  }

  useEffect(() => {
    if (!jobId || lastRunResult?.status === 'completed' || lastRunResult?.status === 'failed') return
    let cancelled = false
    const poll = async () => {
      try {
        const job = await getBacktestJob(jobId)
        if (cancelled) return
        setLastRunResult((current) => ({ ...current, ...job, ...(job.result ?? {}) }))
        setProgress({ history: job.historyProgress ?? 0, backtest: job.backtestProgress ?? 0, phase: job.phase ?? job.status })
        setLastRunMessage(job.message)
        if (job.status === 'completed') void refetch()
      } catch {
        if (!cancelled) setLastRunMessage('Unable to read backtest progress from the backend.')
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 800)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [jobId, lastRunResult?.status, refetch])

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Backtest analytics</p>
          <h2>Performance review</h2>
        </div>
        <span className="pill neutral">Source: FastAPI</span>
      </header>

      <section className="panel page-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Run</p>
            <h3>Launch dry-run backtest</h3>
          </div>
          <button className="primary-action" onClick={handleRunBacktest} disabled={runMutation.isPending}>
            {runMutation.isPending ? 'Queueing…' : 'Run backtest'}
          </button>
        </div>

        {lastRunMessage && (
          <div className="panel-header compact" style={{ marginTop: '18px' }}>
            <span className="pill neutral">{lastRunMessage}</span>
          </div>
        )}

        {jobId && (
          <div className="progress-stack" aria-live="polite">
            <div><span>History download</span><strong>{progress.history}%</strong></div>
            <progress max="100" value={progress.history} />
            <div><span>Backtest calculation</span><strong>{progress.backtest}%</strong></div>
            <progress max="100" value={progress.backtest} />
            <small>Phase: {progress.phase} {lastRunResult?.status === 'completed' ? '• completed' : ''}</small>
          </div>
        )}

        <div className="settings-grid">
          <label className="field-block">
            <span>Pair</span>
            <select value={pair} onChange={(event) => setPair(event.target.value)}>
              <option value="EUR/USD">EUR/USD</option>
              <option value="GBP/USD">GBP/USD</option>
              <option value="USD/JPY">USD/JPY</option>
            </select>
          </label>

          <label className="field-block">
            <span>Timeframe</span>
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              <option value="M5">M5</option>
              <option value="M15">M15</option>
              <option value="H1">H1</option>
            </select>
          </label>

          <label className="field-block">
            <span>History unit</span>
            <select value={historyMode} onChange={(event) => setHistoryMode(event.target.value as 'candles' | 'days')}><option value="candles">Candles</option><option value="days">Days</option></select>
          </label>

          <label className="field-block">
            <span>History value</span>
            <input type="number" value={historyValue} min={1} max={historyMode === 'candles' ? 10000 : 30} onChange={(event) => setHistoryValue(Number(event.target.value) || 1)} />
          </label>
        </div>
      </section>

      {lastRunResult?.status === 'completed' && (
        <section className="summary-grid">
          <article className="summary-card">
            <span>Data source</span>
            <strong>OANDA candles</strong>
          </article>
          <article className="summary-card">
            <span>Strategy</span>
            <strong>AI baseline</strong>
          </article>
          <article className="summary-card">
            <span>Config source</span>
            <strong>{lastRunResult.aiParameters?.configSource ?? 'pair config'}</strong>
          </article>
          <article className="summary-card">
            <span>Net P/L</span>
            <strong>{lastRunResult.netPl ?? '0.00'}</strong>
          </article>
          <article className="summary-card">
            <span>Trades / win rate</span>
            <strong>{lastRunResult.trades ?? 0} / {lastRunResult.winRate ?? '0.00'}%</strong>
          </article>
          <article className="summary-card">
            <span>Max drawdown</span>
            <strong>{lastRunResult.maxDrawdown ?? '0.00'}%</strong>
          </article>
        </section>
      )}

      {lastRunResult?.status === 'completed' && lastRunResult.aiParameters && (
        <section className="panel page-panel">
          <div className="panel-header compact"><div><p className="eyebrow">AI baseline</p><h3>Parameters used</h3></div></div>
          <div className="summary-grid">
            {Object.entries(lastRunResult.aiParameters).map(([key, value]) => <article key={key} className="summary-card"><span>{key}</span><strong>{Array.isArray(value) ? value.join(', ') : value}</strong></article>)}
          </div>
        </section>
      )}

      {lastRunResult?.status === 'completed' && lastRunResult.tradeDetails && (
        <section className="panel">
          <div className="panel-header"><div><p className="eyebrow">Execution map</p><h3>Entry and exit trades</h3></div></div>
          <div className="strategy-table-wrap"><table className="positions-table"><thead><tr><th>Direction</th><th>Entry</th><th>Exit</th><th>Volume</th><th>Leverage</th><th>Gross P/L</th><th>Costs</th><th>Net P/L</th></tr></thead><tbody>{lastRunResult.tradeDetails.map((trade, index) => <tr key={`${trade.entry_time ?? 'trade'}-${index}`}><td>{String(trade.direction ?? '-')}</td><td>{String(trade.entry_price ?? '-')}<br /><small>{String(trade.entry_time ?? '')}</small></td><td>{String(trade.exit_price ?? '-')}<br /><small>{String(trade.exit_time ?? '')}</small></td><td>{String(trade.volume ?? trade.units ?? '-')}</td><td>{String(trade.leverage ?? '-')}</td><td>{String(trade.gross_pl ?? '-')}</td><td>{String(trade.spread_cost ?? '-')}</td><td>{String(trade.net_pl ?? '-')}</td></tr>)}</tbody></table></div>
        </section>
      )}

      {isLoading && <div className="panel page-panel"><p>Loading backtest data from backend…</p></div>}
      {isError && <div className="panel page-panel"><p>Backend analytics endpoint unavailable; fallback data is being used.</p></div>}

      {!isLoading && data && (
        <>
          <section className="summary-grid">
            {summary.map((item) => (
              <article key={item.label} className="summary-card">
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </article>
            ))}
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Runs</p>
                <h3>Recent backtest campaigns</h3>
              </div>
              <button className="quiet-button">Export report</button>
            </div>

            <div className="strategy-table-wrap">
              <table className="positions-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Pair</th>
                    <th>Timeframe</th>
                    <th>Status</th>
                    <th>Result</th>
                    <th>Net P/L</th>
                    <th>Drawdown</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((run) => (
                    <tr key={run.id}>
                      <td>{run.name}</td>
                      <td>{run.pair}</td>
                      <td>{run.timeframe}</td>
                      <td>{run.status}</td>
                      <td>{run.result}</td>
                      <td>{run.netProfit}</td>
                      <td>{run.drawdown}</td>
                      <td>{run.trades}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  )
}
