import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { getAccountSummary, getMarketSummary, submitMarketOrder } from '../api/mockApi'
import { useForexSocket } from '../hooks/useForexSocket'
import { useUiStore } from '../store/useUiStore'

const pnlSeries = [18, 36, 28, 52, 45, 68, 62, 80, 72, 88, 84, 96]

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
  const liveAccount = useUiStore((state) => state.accountFeed)
  const liveMarket = useUiStore((state) => state.marketFeed)
  const userRole = useUiStore((state) => state.userRole)
  const setOrdersFeed = useUiStore((state) => state.setOrdersFeed)

  const account = liveAccount ?? accountQuery.data
  const market = liveMarket ?? marketQuery.data

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

  const statCards = [
    { label: 'Win rate', value: '62.4%', delta: '+4.1%', tone: 'emerald' },
    { label: 'Sharpe', value: '1.84', delta: '+0.26', tone: 'cyan' },
    { label: 'Risk budget', value: '72%', delta: 'Moderate', tone: 'violet' },
    { label: 'Max DD', value: '4.1%', delta: '-0.6%', tone: 'amber' },
  ]

  const marginBreakdown = [
    { pair: 'EUR/USD', value: '$42.6k', share: 42, color: '#67e8f9' },
    { pair: 'GBP/USD', value: '$31.2k', share: 31, color: '#a78bfa' },
    { pair: 'USD/JPY', value: '$18.4k', share: 19, color: '#34d399' },
    { pair: 'AUD/USD', value: '$7.8k', share: 8, color: '#fbbf24' },
  ]

  const equityTrendValues = [38, 42, 48, 47, 55, 58, 64, 60, 66, 71, 74, 82]
  const riskHeatmap = [
    { pair: 'EUR/USD', value: 82, level: 'high' },
    { pair: 'GBP/USD', value: 71, level: 'mid' },
    { pair: 'USD/JPY', value: 58, level: 'safe' },
    { pair: 'AUD/USD', value: 63, level: 'mid' },
    { pair: 'NZD/USD', value: 46, level: 'safe' },
    { pair: 'USD/CAD', value: 74, level: 'high' },
  ]
  const pairLeaderboard = [
    { pair: 'EUR/USD', pnl: '+$1,420', value: '2.4x' },
    { pair: 'GBP/USD', pnl: '+$980', value: '1.8x' },
    { pair: 'USD/JPY', pnl: '-$312', value: '0.8x' },
    { pair: 'AUD/USD', pnl: '+$540', value: '1.1x' },
  ]

  const totalMargin = '$100.0k'
  const usedMargin = '$68.0k'
  const usedPercent = 68
  const donutStyle = {
    background: `conic-gradient(#67e8f9 0 42%, #a78bfa 42% 73%, #34d399 73% 92%, #fbbf24 92% 100%)`,
  }

  const trendPoints = equityTrendValues
    .map((value, index) => `${index * 18 + 10},${80 - value}`)
    .join(' ')

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
                {[
                  { symbol: 'EUR/USD', side: 'Long', units: '2,400', entry: '1.0881', stop: '1.0835', tp: '1.0995', pnl: '+$1,420.20' },
                  { symbol: 'GBP/USD', side: 'Short', units: '1,800', entry: '1.2828', stop: '1.2890', tp: '1.2685', pnl: '+$980.40' },
                  { symbol: 'USD/JPY', side: 'Long', units: '1,200', entry: '147.95', stop: '146.80', tp: '151.20', pnl: '-$312.60' },
                ].map((pos) => (
                  <tr key={pos.symbol}>
                    <td>{pos.symbol}</td>
                    <td className={pos.side === 'Long' ? 'long' : 'short'}>{pos.side}</td>
                    <td>{pos.units}</td>
                    <td>{pos.entry}</td>
                    <td>{pos.stop}</td>
                    <td>{pos.tp}</td>
                    <td className={pos.pnl.startsWith('+') ? 'positive' : 'negative'}>{pos.pnl}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
              {pnlSeries.map((height, index) => (
                <span key={index} style={{ height: `${height}%` }} />
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

          <div className="panel chart-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Trend</p>
                <h3>Equity trend</h3>
              </div>
              <span className="pill positive">+12.4%</span>
            </div>

            <svg className="equity-trend-chart" viewBox="0 0 220 90" preserveAspectRatio="none" aria-label="Equity trend line chart">
              <defs>
                <linearGradient id="equityTrendFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="rgba(56, 189, 248, 0.45)" />
                  <stop offset="100%" stopColor="rgba(56, 189, 248, 0.02)" />
                </linearGradient>
              </defs>
              <path d={`M 0 80 L ${trendPoints} L 200 80 Z`} fill="url(#equityTrendFill)" opacity="0.6" />
              <polyline fill="none" stroke="#67e8f9" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" points={trendPoints} />
            </svg>

            <div className="trend-axis">
              <span>Jan</span>
              <span>Mar</span>
              <span>Jun</span>
              <span>Sep</span>
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
                <p className="eyebrow">Leaderboard</p>
                <h3>Pairs by P/L</h3>
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
