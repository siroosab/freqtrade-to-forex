import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { ForexChart } from '../components/ForexChart'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount } from '../components/chartOptions'
import { closeManualPosition, createManualClientOrderId, getAccountSummary, getBrokerPositions, getMarketQuote, getMarketSummary, getOrdersChart, getRiskSummary, submitMarketOrder, type BrokerTrade } from '../api/mockApi'
import { useForexSocket } from '../hooks/useForexSocket'
import { useUiStore } from '../store/useUiStore'

export function DashboardPage() {
  useForexSocket()
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [closeTarget, setCloseTarget] = useState<BrokerTrade | null>(null)
  const [isClosing, setIsClosing] = useState(false)
  const [closeError, setCloseError] = useState<string | null>(null)
  const [positionView, setPositionView] = useState<'open' | 'closed'>('open')
  const selectedInstruments = useUiStore((state) => state.selectedInstruments)
  const [instrument, setInstrument] = useState(selectedInstruments[0] ?? 'EUR/USD')
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [units, setUnits] = useState(1200)
  const [riskPercent, setRiskPercent] = useState(0.75)
  const [stopLossPercent, setStopLossPercent] = useState(0.55)
  const [takeProfitPercent, setTakeProfitPercent] = useState(1)
  const [orderStatus, setOrderStatus] = useState<{ status: string; orderId?: string; transactionId?: string; fillPrice?: string | null; environment?: string; reason?: string | null; cancelReason?: string | null } | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const accountQuery = useQuery({ queryKey: ['account'], queryFn: getAccountSummary })
  const marketQuery = useQuery({ queryKey: ['market'], queryFn: getMarketSummary })
  const quoteQuery = useQuery({ queryKey: ['market-quote', instrument], queryFn: () => getMarketQuote(instrument), refetchInterval: 1500, retry: false })
  const positionsQuery = useQuery({ queryKey: ['broker-positions'], queryFn: getBrokerPositions, refetchInterval: 3000 })
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
  const brokerQuote = quoteQuery.data
  const displayQuote = brokerQuote ?? market?.instruments.find((item) => item.pair === instrument)

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

  const entryPrice = brokerQuote ? Number(side === 'BUY' ? brokerQuote.ask : brokerQuote.bid) : Number.NaN
  const pricePrecision = brokerQuote?.displayPrecision ?? (instrument.endsWith('/JPY') ? 3 : 5)
  const protectionPrice = (percent: number, kind: 'stop' | 'target') => {
    if (!Number.isFinite(entryPrice) || entryPrice <= 0 || !Number.isFinite(percent) || percent <= 0) return ''
    const movesWithPosition = (kind === 'target') === (side === 'BUY')
    const multiplier = 1 + (movesWithPosition ? 1 : -1) * percent / 100
    return (entryPrice * multiplier).toFixed(pricePrecision)
  }
  const stopLossPrice = protectionPrice(stopLossPercent, 'stop')
  const takeProfitPrice = protectionPrice(takeProfitPercent, 'target')
  const minimumTradeSize = Number(brokerQuote?.minimumTradeSize ?? '1')
  const validUnits = Number.isInteger(units) && units >= minimumTradeSize
  const validProtectionPrices = Boolean(stopLossPrice && takeProfitPrice)
    && (side === 'BUY'
      ? Number(stopLossPrice) < entryPrice && Number(takeProfitPrice) > entryPrice
      : Number(stopLossPrice) > entryPrice && Number(takeProfitPrice) < entryPrice)
  const orderValidationMessage = brokerQuote && !validUnits
    ? `Units must be a whole number of at least ${minimumTradeSize}.`
    : brokerQuote && !validProtectionPrices
      ? 'Stop loss and take profit must be valid prices on opposite sides of the entry.'
      : null
  const canSubmitOrder = userRole !== 'viewer' && !isSubmitting && brokerQuote?.tradeable === true && Number.isFinite(entryPrice) && validUnits && validProtectionPrices

  const handleOrderSubmit = async () => {
    if (!canSubmitOrder) {
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
          stopLoss: stopLossPrice,
          takeProfit: takeProfitPrice,
          riskPercent,
          clientOrderId: createManualClientOrderId(),
        },
        userRole,
      )
      const nextOrder = {
        id: result.orderId ?? `ui-order-${Date.now()}`,
        symbol: result.symbol,
        side: result.side === 'SELL' ? 'SELL' : 'BUY',
        volume: result.volume ?? String(units),
        status: result.status === 'filled' ? 'Filled' : result.status === 'cancelled' ? 'Cancelled' : ['queued', 'accepted'].includes(result.status) ? 'Pending' : 'Rejected',
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
      await queryClient.invalidateQueries({ queryKey: ['broker-positions'] })
      await queryClient.invalidateQueries({ queryKey: ['orders'] })
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

  const handlePositionClose = async () => {
    if (!closeTarget || userRole === 'viewer') return
    setIsClosing(true)
    setCloseError(null)
    try {
      await closeManualPosition(closeTarget.id, userRole)
      await queryClient.invalidateQueries({ queryKey: ['broker-positions'] })
      setCloseTarget(null)
    } catch (error) {
      setCloseError(error instanceof Error ? error.message : 'Position close rejected by broker')
    } finally {
      setIsClosing(false)
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
  const visiblePositions = positionsQuery.data?.[positionView] ?? []

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
                <p className="eyebrow">Broker positions</p>
                <h3>Trade monitor</h3>
              </div>
              <div className="segmented" role="tablist" aria-label="Trade status">
                <button type="button" role="tab" aria-selected={positionView === 'open'} className={positionView === 'open' ? 'segment active' : 'segment'} onClick={() => setPositionView('open')}>Open</button>
                <button type="button" role="tab" aria-selected={positionView === 'closed'} className={positionView === 'closed' ? 'segment active' : 'segment'} onClick={() => setPositionView('closed')}>Closed</button>
              </div>
            </div>

            {positionsQuery.isError && <p className="inline-error">Broker trade data unavailable. Check the Practice account connection.</p>}
            <div className="position-table-scroll">
              <table className="positions-table broker-positions-table">
                <thead>
                  <tr>
                    <th>Source</th><th>Symbol</th><th>Side</th><th>Units</th><th>Entry</th>
                    <th>{positionView === 'open' ? 'Current' : 'Exit'}</th><th>Stop</th><th>TP</th><th>P/L</th><th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {visiblePositions.map((position) => (
                    <tr key={position.id} className={position.manual ? 'manual-position-row' : undefined}>
                      <td><span className={position.manual ? 'manual-trade-tag' : 'strategy-trade-tag'}>{position.manual ? 'Manual' : 'Strategy'}</span></td>
                      <td>{position.symbol}</td>
                      <td className={position.side === 'BUY' ? 'long' : 'short'}>{position.side}</td>
                      <td>{Number(position.units).toLocaleString()}</td>
                      <td>{position.entryPrice || '—'}</td>
                      <td>{positionView === 'open' ? position.currentPrice ?? '—' : position.exitPrice ?? '—'}</td>
                      <td>{position.stopLoss ?? '—'}</td>
                      <td>{position.takeProfit ?? '—'}</td>
                      <td className={Number(position.pnl) < 0 ? 'negative' : 'positive'}>{Number(position.pnl) > 0 ? '+' : ''}{position.pnl}</td>
                      <td>{positionView === 'open' && position.manual && userRole !== 'viewer' ? <button className="close-position-button" type="button" onClick={() => { setCloseError(null); setCloseTarget(position) }}>Close</button> : positionView === 'closed' ? <span className="muted-cell">Closed</span> : '—'}</td>
                    </tr>
                  ))}
                  {!positionsQuery.isLoading && visiblePositions.length === 0 && <tr><td className="empty-cell" colSpan={10}>{positionsQuery.isError ? 'Broker positions could not be loaded.' : `No ${positionView} broker trades.`}</td></tr>}
                </tbody>
              </table>
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
            <div className="quote-strip">
              <div className="quote-strip-heading"><strong>{activeInstrument}</strong><span className={brokerQuote?.tradeable ? 'quote-live' : 'quote-delayed'}>{brokerQuote?.tradeable ? 'PRACTICE LIVE' : quoteQuery.isError ? 'QUOTE UNAVAILABLE' : 'WAITING FOR BROKER'}</span></div>
              <div className="quote-values">
                <div><span>Bid</span><strong>{displayQuote ? Number(displayQuote.bid).toFixed(pricePrecision) : '—'}</strong></div>
                <div><span>Ask</span><strong>{displayQuote ? Number(displayQuote.ask).toFixed(pricePrecision) : '—'}</strong></div>
                <div><span>Spread</span><strong>{displayQuote ? Number(displayQuote.spread).toFixed(pricePrecision) : '—'}</strong></div>
              </div>
              <small>{brokerQuote ? `Updated ${new Date(brokerQuote.time).toLocaleTimeString()} · ${brokerQuote.environment}` : quoteQuery.isError ? 'No broker quote. Orders are disabled.' : 'Connecting to broker quote…'}</small>
            </div>

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
                <input type="number" min={minimumTradeSize} step="1" value={units} onChange={(event) => setUnits(Number(event.target.value) || 0)} />
              </label>
              <label>
                <span>Risk %</span>
                <input type="number" step="0.01" value={riskPercent} onChange={(event) => setRiskPercent(Number(event.target.value) || 0)} />
              </label>
            </div>

            <div className="field-row">
              <label>
                <span>Stop loss distance (%)</span>
                <input type="number" min="0.01" step="0.01" value={stopLossPercent} onChange={(event) => setStopLossPercent(Number(event.target.value) || 0)} />
                <small className="calculated-price">Broker stop: {stopLossPrice || '—'}</small>
              </label>
              <label>
                <span>Take profit distance (%)</span>
                <input type="number" min="0.01" step="0.01" value={takeProfitPercent} onChange={(event) => setTakeProfitPercent(Number(event.target.value) || 0)} />
                <small className="calculated-price">Broker target: {takeProfitPrice || '—'}</small>
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

            {orderValidationMessage && <small className="calculated-price" role="alert">{orderValidationMessage}</small>}

            <div className="actions-row">
              <button
                className="primary-action"
                disabled={!canSubmitOrder}
                onClick={() => setConfirmOpen(true)}
                style={{ opacity: canSubmitOrder ? 1 : 0.5 }}
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
            {quoteQuery.isError && <p className="inline-error">Live broker pricing is required before an order can be submitted.</p>}
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
            <h3>Confirm Practice order</h3>
            <p>
              Submit a {side} order for <strong>{activeInstrument}</strong> at market. Entry reference {entryPrice.toFixed(pricePrecision)}, stop {stopLossPrice} ({stopLossPercent}%), target {takeProfitPrice} ({takeProfitPercent}%).
            </p>
            <div className="modal-actions">
              <button className="secondary-action" onClick={() => setConfirmOpen(false)}>
                Cancel
              </button>
              <button className="primary-action" disabled={!canSubmitOrder} onClick={handleOrderSubmit}>
                Confirm Practice order
              </button>
            </div>
          </div>
        </div>
      )}

      {closeTarget && (
        <div className="modal-backdrop" onClick={() => { if (!isClosing) setCloseTarget(null) }}>
          <div className="confirm-modal" onClick={(event) => event.stopPropagation()}>
            <p className="eyebrow">Manual position</p>
            <h3>Close {closeTarget.symbol} {closeTarget.side}?</h3>
            <p>Close all {Number(closeTarget.units).toLocaleString()} units at the broker’s current market price. This cannot be undone.</p>
            {closeError && <p className="inline-error">{closeError}</p>}
            <div className="modal-actions">
              <button className="secondary-action" disabled={isClosing} onClick={() => setCloseTarget(null)}>Cancel</button>
              <button className="danger-action" disabled={isClosing} onClick={handlePositionClose}>{isClosing ? 'Closing…' : 'Confirm close'}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
