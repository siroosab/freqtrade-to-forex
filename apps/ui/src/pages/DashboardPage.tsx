import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getAccountSummary, getMarketSummary, submitMarketOrder } from '../api/mockApi'
import { useForexSocket } from '../hooks/useForexSocket'
import { useUiStore } from '../store/useUiStore'

const pnlSeries = [18, 36, 28, 52, 45, 68, 62, 80, 72, 88, 84, 96]

export function DashboardPage() {
  useForexSocket()
  const [confirmOpen, setConfirmOpen] = useState(false)

  const accountQuery = useQuery({ queryKey: ['account'], queryFn: getAccountSummary })
  const marketQuery = useQuery({ queryKey: ['market'], queryFn: getMarketSummary })
  const liveAccount = useUiStore((state) => state.accountFeed)
  const liveMarket = useUiStore((state) => state.marketFeed)
  const userRole = useUiStore((state) => state.userRole)

  const account = liveAccount ?? accountQuery.data
  const market = liveMarket ?? marketQuery.data

  const handleOrderSubmit = async () => {
    if (userRole === 'viewer') {
      return
    }

    try {
      await submitMarketOrder(
        {
          symbol: 'EUR/USD',
          side: 'BUY',
          volume: '1200',
        },
        userRole,
      )
      setConfirmOpen(false)
    } catch {
      setConfirmOpen(false)
    }
  }

  const metrics = [
    { label: 'Account Equity', value: account?.equity ?? '$0.00', delta: '+1.84%', tone: 'positive' },
    { label: 'Open Exposure', value: account?.exposure ?? '$0.00', delta: '-0.42%', tone: 'negative' },
    { label: 'Net P/L', value: account?.netPnl ?? '$0.00', delta: '+3.12%', tone: 'positive' },
    { label: 'Margin Used', value: account?.marginUsed ?? '0%', delta: 'Healthy', tone: 'neutral' },
  ]

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
                <select defaultValue="EUR/USD">
                  <option>EUR/USD</option>
                  <option>GBP/USD</option>
                  <option>USD/JPY</option>
                </select>
              </label>
              <label>
                <span>Side</span>
                <select defaultValue="BUY">
                  <option>BUY</option>
                  <option>SELL</option>
                </select>
              </label>
            </div>

            <div className="field-row">
              <label>
                <span>Units</span>
                <input defaultValue="1200" />
              </label>
              <label>
                <span>Risk %</span>
                <input defaultValue="0.75" />
              </label>
            </div>

            <div className="field-row">
              <label>
                <span>Stop loss</span>
                <input defaultValue="1.0835" />
              </label>
              <label>
                <span>Take profit</span>
                <input defaultValue="1.0995" />
              </label>
            </div>

            <div className="actions-row">
              <button
                className="primary-action"
                disabled={userRole === 'viewer'}
                onClick={() => setConfirmOpen(true)}
                style={{ opacity: userRole === 'viewer' ? 0.5 : 1 }}
              >
                Submit order
              </button>
              <button className="secondary-action">Preview</button>
            </div>
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
              This action would submit a live order for <strong>EUR/USD</strong> with risk policy checks and a client order id.
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
