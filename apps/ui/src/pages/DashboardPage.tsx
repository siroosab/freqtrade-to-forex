import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { ForexChart } from '../components/ForexChart'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount } from '../components/chartOptions'
import { getAccountSummary, getMarketSummary, getOrdersChart, getRiskSummary, submitMarketOrder } from '../api/mockApi'
import { useForexSocket } from '../hooks/useForexSocket'
import { useUiStore } from '../store/useUiStore'

export function DashboardPage() {
  useForexSocket()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const selectedInstruments = useUiStore((state) => state.selectedInstruments)
  const [instrument, setInstrument] = useState(selectedInstruments[0] ?? 'EUR/USD')
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [units, setUnits] = useState(1200)
  const [riskPercent, setRiskPercent] = useState(0.75)
  const [stopLoss, setStopLoss] = useState('1.0835')
  const [takeProfit, setTakeProfit] = useState('1.0995')
  const [orderStatus, setOrderStatus] = useState<{ status: string; orderId?: string; transactionId?: string; fillPrice?: string | null; environment?: string; reason?: string | null; cancelReason?: string | null } | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const accountQuery = useQuery({ queryKey: ['account'], queryFn: getAccountSummary })
  const marketQuery = useQuery({ queryKey: ['market'], queryFn: getMarketSummary })
  const riskQuery = useQuery({ queryKey: ['risk'], queryFn: getRiskSummary })
  const [chartTimeframe, setChartTimeframe] = useState('H1')
  const [chartCountMultiplier, setChartCountMultiplier] = useState(1)
  const chartQuery = useQuery({
    queryKey: ['dashboard-chart', instrument, chartTimeframe, chartCountMultiplier],
    queryFn: () => getOrdersChart(instrument, chartTimeframe, chartCandleCount(chartCountMultiplier)),
    refetchInterval: 60000,
  })
  const liveAccount = useUiStore((state) => state.accountFeed)
  const liveMarket = useUiStore((state) => state.marketFeed)
  const userRole = useUiStore((state) => state.userRole)
  const setOrdersFeed = useUiStore((state) => state.setOrdersFeed)

  const account = liveAccount ?? accountQuery.data
  const market = liveMarket ?? marketQuery.data
  const riskSummary = riskQuery.data

  const instrumentOptions = selectedInstruments.length ? selectedInstruments : ['EUR/USD', 'GBP/USD', 'USD/JPY']
  const activeInstrument = instrumentOptions.includes(instrument) ? instrument : instrumentOptions[0] ?? 'EUR/USD'

  useEffect(() => {
    if (instrument !== activeInstrument) {
      setInstrument(activeInstrument)
    }
  }, [activeInstrument, instrument])

  const recommendedUnits = useMemo(() => {
    if (!Number.isFinite(units) || units <= 0) return 0
    return Math.max(100, Math.round(units))
  }, [units])

  const handleOrderSubmit = async () => {
    if (userRole === 'viewer') {
      return
    }

    setIsSubmitting(true)
    try {
      const result = await submitMarketOrder(
        {
          symbol: instrument,
          side,
          volume: units,
          units,
          stopLoss,
          takeProfit,
        },
        userRole,
      )
      const nextOrder = {
        id: result.orderId ?? `ui-order-${Date.now()}`,
        symbol: result.symbol,
        side: result.side === 'SELL' ? 'SELL' : 'BUY',
        volume: result.volume ?? String(units),
        status: result.status === 'filled' ? 'Filled' : result.status === 'cancelled' ? 'Cancelled' : result.status === 'queued' ? 'Pending' : 'Rejected',
        createdAt: new Date().toISOString(),
        risk: `${riskPercent}%`,
      } as const
      setOrderStatus({
        status: result.status,
        orderId: result.orderId,
        transactionId: result.transactionId,
        fillPrice: result.fillPrice,
        environment: result.environment,
        reason: result.reason ?? result.cancelReason ?? null,
        cancelReason: result.cancelReason ?? result.reason ?? null,
      })
      const feed = useUiStore.getState().ordersFeed ?? []
      setOrdersFeed([nextOrder, ...feed].slice(0, 10))
      setConfirmOpen(false)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Order submission rejected by backend'
      setOrderStatus({
        status: 'rejected',
        orderId: undefined,
        transactionId: undefined,
        fillPrice: null,
        environment: undefined,
        reason: message,
        cancelReason: message,
      })
      console.error(error)
    } finally {
      setIsSubmitting(false)
    }
  }

  const metrics = [
    { label: 'Account Equity', value: account?.equity ?? '$0.00', delta: '+1.84%', tone: 'positive' },
    { label: 'Open Exposure', value: account?.exposure ?? '$0.00', delta: '-0.42%', tone: 'negative' },
    { label: 'Net P/L', value: account?.netPnl ?? '$0.00', delta: '+3.12%', tone: 'positive' },
    { label: 'Margin Used', value: account?.marginUsed ?? '0%', delta: 'Healthy', tone: 'neutral' },
  ]

  const parseAmountValue = (value: string | undefined) => {
    if (!value) return 0
    const cleaned = value.replace(/[$,%\s]/g, '').replace(/k/gi, '000').replace(/m/gi, '000000')
    const parsed = Number.parseFloat(cleaned)
    return Number.isFinite(parsed) ? parsed : 0
  }

  const statCards = [
    { label: 'Net P/L', value: account?.netPnl ?? '$0.00', delta: '+3.12%', tone: 'emerald' },
    { label: 'Margin level', value: riskSummary?.marginLevel ?? account?.marginUsed ?? '0%', delta: account?.marginUsed ?? '0%', tone: 'cyan' },
    { label: 'Daily risk', value: riskSummary?.dailyLoss ?? account?.dailyRisk ?? '0%', delta: 'Policy', tone: 'violet' },
    { label: 'Drawdown', value: account?.drawdown ?? '0%', delta: 'Max DD', tone: 'amber' },
  ]

  const marginBreakdown = (riskSummary?.exposureByPair ?? []).map((item, index) => {
    const colors = ['#67e8f9', '#a78bfa', '#34d399', '#fbbf24']
    const total = riskSummary?.exposureByPair.reduce((sum, entry) => sum + parseAmountValue(entry.value), 0) ?? 0
    const share = total > 0 ? Math.round((parseAmountValue(item.value) / total) * 100) : 0
    return { pair: item.pair, value: item.value, share, color: colors[index % colors.length] }
  })

  const totalMargin = riskSummary ? `${(riskSummary.exposureByPair.reduce((sum, item) => sum + parseAmountValue(item.value), 0) / 1000).toFixed(1)}k` : '$0.0k'
  const usedPercent = account?.marginUsed ? Number.parseFloat(account.marginUsed.replace('%', '')) || 0 : 0
  const usedMargin = account?.marginUsed ?? '0%'
  const donutStyle = {
    background: marginBreakdown.length
      ? `conic-gradient(${marginBreakdown.map((item, index) => {
          const start = marginBreakdown.slice(0, index).reduce((sum, current) => sum + current.share, 0)
          return `${item.color} ${start}% ${start + item.share}%`
        }).join(', ')})`
      : 'conic-gradient(#67e8f9 0 100%)',
  }

  const riskHeatmap = (riskSummary?.exposureByPair ?? []).map((item) => {
    const value = Math.min(100, Math.max(20, parseAmountValue(item.value) / 1000))
    const level = value >= 80 ? 'high' : value >= 55 ? 'mid' : 'safe'
    return { pair: item.pair, value: Math.round(value), level }
  })

  const pairLeaderboard = (riskSummary?.exposureByPair ?? []).map((item, index) => ({
    pair: item.pair,
    pnl: `${item.value}`,
    value: `${index + 1}. ${item.value}`,
  }))

  const chartCandles = chartQuery.data?.candles ?? []
  const equityTrendValues = chartCandles.slice(-12).map((candle) => Number(candle.close))
  const sparklineValues = equityTrendValues.length
    ? equityTrendValues.map((value, _, values) => {
        const min = Math.min(...values)
        const max = Math.max(...values)
        const spread = max - min || 1
        return ((value - min) / spread) * 100
      })
    : [20, 26, 32, 28, 35, 38, 44, 56, 52, 61, 58, 66]
  const portfolioRows = (riskSummary?.exposureByPair ?? []).map((item) => ({
    symbol: item.pair,
    side: 'Long',
    units: `${Math.round(parseAmountValue(item.value) / 120)}`,
    entry: market?.instruments.find((instrumentItem) => instrumentItem.pair === item.pair)?.bid?.toFixed(4) ?? '—',
    stop: '—',
    tp: '—',
    pnl: item.value,
  }))

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h2>Trading overview</h2>
        </div>

        <div className="topbar-actions">
          <div className="search-pill">Search instrument</div>
          <div className="status-pill online">System online</div>
          <div className="avatar">AD</div>
        </div>
      </header>

      <section className="metrics-grid">
        {metrics.map((metric) => (
          <article key={metric.label} className="metric-card">
            <div className="metric-head">
              <span>{metric.label}</span>
              <span className={`delta ${metric.tone}`}>{metric.delta}</span>
            </div>
            <strong>{metric.value}</strong>
          </article>
        ))}
      </section>

      <section className="stat-card-row">
        {statCards.map((card) => (
          <article key={card.label} className={`mini-stat-card tone-${card.tone}`}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <small>{card.delta}</small>
          </article>
        ))}
      </section>

      <section className="content-grid">
        <div className="main-column">
          <div className="panel chart-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Trend</p>
                <h3>Equity trend</h3>
              </div>
              <ChartDataControls timeframe={chartTimeframe} countMultiplier={chartCountMultiplier} onTimeframeChange={setChartTimeframe} onCountMultiplierChange={setChartCountMultiplier} />
            </div>

            {chartQuery.isLoading && <p className="chart-empty-state">Loading real chart data…</p>}
            {chartQuery.isError && <p className="chart-empty-state">Real chart data unavailable; retrying from backend.</p>}
            {chartQuery.data && (
              <div className="real-chart-panel">
                <ForexChart data={chartQuery.data} />
              </div>
            )}
          </div>

          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Live feed</p>
                <h3>Market watchlist</h3>
              </div>
              <div className="segmented">
                <button className="segment active">M1</button>
                <button className="segment">M5</button>
                <button className="segment">H1</button>
              </div>
            </div>

            <div className="watchlist">
              {market?.instruments.map((item) => (
                <div key={item.pair} className="watch-row">
                  <div className="pair-block">
                    <span className="pair-name">{item.pair}</span>
                    <span className="pair-change positive">{item.change}</span>
                  </div>
                  <div className="price-block">
                    <span>Bid</span>
                    <strong>{item.bid}</strong>
                  </div>
                  <div className="price-block">
                    <span>Ask</span>
                    <strong>{item.ask}</strong>
                  </div>
                  <div className="price-block">
                    <span>Spread</span>
                    <strong>{item.spread}</strong>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Open positions</p>
                <h3>Portfolio</h3>
              </div>
              <button className="quiet-button">View all</button>
            </div>

            <table className="positions-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Units</th>
                  <th>Entry</th>
                  <th>Stop</th>
                  <th>TP</th>
                  <th>P/L</th>
                </tr>
              </thead>
              <tbody>
                {portfolioRows.map((pos) => (
                  <tr key={pos.symbol}>
                    <td>{pos.symbol}</td>
                    <td className={pos.side === 'Long' ? 'long' : 'short'}>{pos.side}</td>
                    <td>{pos.units}</td>
                    <td>{pos.entry}</td>
                    <td>{pos.stop}</td>
                    <td>{pos.tp}</td>
                    <td className={pos.pnl.startsWith('-') ? 'negative' : 'positive'}>{pos.pnl}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Exposure</p>
                <h3>Risk heatmap</h3>
              </div>
            </div>

            <div className="heatmap-grid">
              {riskHeatmap.map((cell) => (
                <div key={cell.pair} className={`heatmap-card ${cell.level}`}>
                  <span>{cell.pair}</span>
                  <strong>{cell.value}</strong>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Alerts</p>
                <h3>Risk & events</h3>
              </div>
            </div>

            <div className="alert-list">
              {market?.alerts.map((alert, index) => (
                <div key={`${alert.title}-${index}`} className="alert-item">
                  <span className="alert-dot" />
                  <div>
                    <strong>{alert.title}</strong>
                    <p>{alert.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="side-column">
          <div className="panel chart-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Margin overview</p>
                <h3>Portfolio allocation</h3>
              </div>
              <span className="pill neutral">{usedPercent}% used</span>
            </div>

            <div className="margin-donut-shell">
              <div className="margin-donut" style={donutStyle} aria-label="Margin allocation donut chart">
                <div className="margin-donut-center">
                  <span>Total margin</span>
                  <strong>{totalMargin}</strong>
                  <small>{usedMargin} used</small>
                </div>
              </div>
            </div>

            <div className="margin-breakdown" aria-label="Margin breakdown by pair">
              {marginBreakdown.map((item) => (
                <div key={item.pair} className="margin-breakdown-row">
                  <div className="margin-meta">
                    <span className="margin-swatch" style={{ background: item.color }} />
                    <span>{item.pair}</span>
                  </div>
                  <div className="margin-values">
                    <strong>{item.value}</strong>
                    <small>{item.share}%</small>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="panel chart-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Balance curve</p>
                <h3>Equity</h3>
              </div>
              <span className="pill positive">+3.12%</span>
            </div>

            <div className="sparkline" aria-label="Equity sparkline">
              {sparklineValues.map((height, index) => (
                <span key={`${height}-${index}`} style={{ height: `${Math.max(16, Math.min(100, height))}%` }} />
              ))}
            </div>

            <div className="risk-summary">
              <div>
                <span>Daily risk</span>
                <strong>{account?.dailyRisk ?? '0.72%'}</strong>
              </div>
              <div>
                <span>Max drawdown</span>
                <strong>{account?.drawdown ?? '4.10%'}</strong>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Leaderboard</p>
                <h3>Pairs by exposure</h3>
              </div>
            </div>

            <div className="leaderboard-list">
              {pairLeaderboard.map((item, index) => (
                <div key={item.pair} className="leaderboard-row">
                  <span className="leaderboard-rank">#{index + 1}</span>
                  <div className="leaderboard-meta">
                    <strong>{item.pair}</strong>
                    <span>{item.value}</span>
                  </div>
                  <span className={item.pnl.startsWith('+') ? 'leaderboard-pnl positive' : 'leaderboard-pnl negative'}>{item.pnl}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Strategy engine</p>
                <h3>Signals</h3>
              </div>
            </div>

            <div className="strategy-stack">
              {market?.strategySignals.map((strategy) => (
                <div key={strategy.name} className="strategy-card">
                  <div className="strategy-row">
                    <strong>{strategy.name}</strong>
                    <span className="pill neutral">{strategy.mode}</span>
                  </div>
                  <div className="strategy-meta">
                    <span>{strategy.status}</span>
                    <span>{strategy.quality}</span>
                  </div>
                  <p>{strategy.signal}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="lower-grid">
        <div className="panel order-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Execution controls</p>
              <h3>Order ticket</h3>
            </div>
          </div>

          <div className="order-form">
            <div className="field-row">
              <label>
                <span>Instrument</span>
                <select value={activeInstrument} onChange={(event) => setInstrument(event.target.value)}>
                  {instrumentOptions.map((pair) => (
                    <option key={pair} value={pair}>{pair}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>Side</span>
                <select value={side} onChange={(event) => setSide(event.target.value as 'BUY' | 'SELL')}>
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </label>
            </div>

            <div className="field-row">
              <label>
                <span>Units</span>
                <input type="number" value={units} onChange={(event) => setUnits(Number(event.target.value) || 0)} />
              </label>
              <label>
                <span>Risk %</span>
                <input type="number" step="0.01" value={riskPercent} onChange={(event) => setRiskPercent(Number(event.target.value) || 0)} />
              </label>
            </div>

            <div className="field-row">
              <label>
                <span>Stop loss</span>
                <input value={stopLoss} onChange={(event) => setStopLoss(event.target.value)} />
              </label>
              <label>
                <span>Take profit</span>
                <input value={takeProfit} onChange={(event) => setTakeProfit(event.target.value)} />
              </label>
            </div>

            <div className="field-row compact-row">
              <div className="recommendation-box">
                <span>Suggested size</span>
                <strong>{recommendedUnits.toLocaleString()} units</strong>
              </div>
              <div className="status-box">
                <span>Order status</span>
                <strong>{orderStatus ? orderStatus.status : 'Idle'}</strong>
              </div>
            </div>

            <div className="actions-row">
              <button
                className="primary-action"
                disabled={userRole === 'viewer' || isSubmitting}
                onClick={() => setConfirmOpen(true)}
                style={{ opacity: userRole === 'viewer' || isSubmitting ? 0.5 : 1 }}
              >
                {isSubmitting ? 'Submitting...' : 'Submit order'}
              </button>
              <button className="secondary-action" type="button" onClick={() => setOrderStatus(null)}>Reset</button>
            </div>

            {orderStatus && (
              <div className="status-rail">
                <div><span>Environment</span><strong>{orderStatus.environment ?? 'practice'}</strong></div>
                <div><span>Order ID</span><strong>{orderStatus.orderId ?? '—'}</strong></div>
                <div><span>Transaction</span><strong>{orderStatus.transactionId ?? '—'}</strong></div>
                <div><span>Fill price</span><strong>{orderStatus.fillPrice ?? '—'}</strong></div>
                <div><span>Reason</span><strong>{orderStatus.reason ?? orderStatus.cancelReason ?? '—'}</strong></div>
              </div>
            )}
          </div>
        </div>

        <div className="panel execution-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Operations log</p>
              <h3>Execution timeline</h3>
            </div>
          </div>

          <div className="timeline">
            <div className="timeline-item">
              <span className="dot success" />
              <div>
                <strong>Order validated</strong>
                <p>Client order ID confirmed and risk policy accepted.</p>
              </div>
              <time>09:14:22</time>
            </div>

            <div className="timeline-item">
              <span className="dot warning" />
              <div>
                <strong>Market event</strong>
                <p>London session opened with a higher volatility band.</p>
              </div>
              <time>09:20:05</time>
            </div>

            <div className="timeline-item">
              <span className="dot neutral" />
              <div>
                <strong>Trade snapshot</strong>
                <p>Position net exposure and margin usage were recalculated.</p>
              </div>
              <time>09:26:11</time>
            </div>
          </div>
        </div>
      </section>

      {confirmOpen && (
        <div className="modal-backdrop" onClick={() => setConfirmOpen(false)}>
          <div className="confirm-modal" onClick={(event) => event.stopPropagation()}>
            <p className="eyebrow">Write action requires confirmation</p>
            <h3>Confirm order submission</h3>
            <p>
              This action would submit a live order for <strong>{activeInstrument}</strong> with risk policy checks and a client order id.
            </p>
            <div className="modal-actions">
              <button className="secondary-action" onClick={() => setConfirmOpen(false)}>
                Cancel
              </button>
              <button className="primary-action" onClick={handleOrderSubmit}>
                Confirm & submit
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
