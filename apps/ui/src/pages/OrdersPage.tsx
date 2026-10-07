import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getOrders, getOrdersChart, type OrderHistoryStatus } from '../api/mockApi'
import { ForexChart } from '../components/ForexChart'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount } from '../components/chartOptions'
import { useUiStore } from '../store/useUiStore'

export function OrdersPage() {
  const [status, setStatus] = useState<OrderHistoryStatus>('all')
  const [limit, setLimit] = useState(50)
  const ordersQuery = useQuery({
    queryKey: ['orders', status, limit],
    queryFn: () => getOrders(status, limit),
    refetchInterval: 15_000,
    retry: false,
  })
  const availablePairs = useUiStore((state) => state.selectedInstruments)
  const [selectedPair, setSelectedPair] = useState(availablePairs[0] ?? 'EUR/USD')
  const pair = availablePairs.length && !availablePairs.includes(selectedPair)
    ? availablePairs[0]
    : selectedPair
  const [timeframe, setTimeframe] = useState('M15')
  const [countMultiplier, setCountMultiplier] = useState(1)
  const chartQuery = useQuery({ queryKey: ['orders-chart', pair, timeframe, countMultiplier], queryFn: () => getOrdersChart(pair, timeframe, chartCandleCount(countMultiplier)), refetchInterval: 30000 })
  const orders = ordersQuery.data?.orders ?? []

  const formatPnl = (value: string | null, currency: string) => {
    if (value === null) return '—'
    const amount = Number(value)
    if (!Number.isFinite(amount)) return '—'
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      currencyDisplay: 'code',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount)
  }

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Orders</p>
          <h2>Execution log</h2>
          <p className="orders-source-note">Live OANDA account data · refreshed every 15 seconds · transaction history up to {ordersQuery.data?.historyDays ?? 365} days</p>
        </div>
      </header>

      <section className="panel page-panel">
        <div className="panel-header compact">
          <div><p className="eyebrow">Market context</p><h3>Signals and trade map</h3></div>
          <div className="chart-controls"><select aria-label="Chart pair" value={pair} onChange={(event) => setSelectedPair(event.target.value)}>{(availablePairs.length ? availablePairs : ['EUR/USD','GBP/USD','USD/JPY']).map((item) => <option key={item} value={item}>{item}</option>)}</select><ChartDataControls timeframe={timeframe} countMultiplier={countMultiplier} onTimeframeChange={setTimeframe} onCountMultiplierChange={setCountMultiplier} /></div>
        </div>
        {chartQuery.isLoading && <p>Loading broker candles and approved strategy signals…</p>}
        {chartQuery.isError && <p role="alert">{chartQuery.error instanceof Error ? chartQuery.error.message : 'Chart data unavailable from the broker API.'}</p>}
        {chartQuery.data && <ForexChart data={chartQuery.data} />}
      </section>

      <section className="panel page-panel">
        <div className="orders-panel-header">
          <div>
            <p className="eyebrow">Broker history</p>
            <h3>Orders and trades</h3>
          </div>
          <div className="orders-filters">
            <label>
              <span>Status</span>
              <select value={status} onChange={(event) => setStatus(event.target.value as OrderHistoryStatus)}>
                <option value="all">All statuses</option>
                <option value="open">Open trades</option>
                <option value="closed">Closed trades</option>
                <option value="filled">Filled orders</option>
                <option value="pending">Pending orders</option>
                <option value="cancelled">Cancelled</option>
                <option value="rejected">Rejected</option>
              </select>
            </label>
            <label>
              <span>Show</span>
              <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
                <option value={25}>25 rows</option>
                <option value={50}>50 rows</option>
                <option value={100}>100 rows</option>
                <option value={250}>250 rows</option>
              </select>
            </label>
          </div>
        </div>

        {ordersQuery.isLoading && <p className="orders-message">Loading live OANDA order history…</p>}
        {ordersQuery.isError && (
          <p className="orders-message orders-error" role="alert">
            {ordersQuery.error instanceof Error ? ordersQuery.error.message : 'Unable to load live OANDA order history.'}
          </p>
        )}
        {!ordersQuery.isLoading && !ordersQuery.isError && orders.length === 0 && (
          <p className="orders-message">No OANDA orders or trades match this filter.</p>
        )}
        {orders.length > 0 && (
          <>
            <p className="orders-result-count">Showing {orders.length} of {ordersQuery.data?.total ?? orders.length} matching records fetched from OANDA</p>
            <div className="position-table-scroll">
              <table className="positions-table orders-history-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Volume</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th>P/L</th>
                    <th>Risk</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((order) => (
                    <tr key={order.transactionId ?? `${order.source}-${order.id}`}>
                      <td className="orders-id-cell" title={order.reason ?? undefined}>{order.id || '—'}</td>
                      <td>{order.symbol || '—'}</td>
                      <td className={order.side === 'BUY' ? 'order-side-buy' : 'order-side-sell'}>{order.side}</td>
                      <td>{Number(order.volume).toLocaleString()}</td>
                      <td><span className={`order-status-badge status-${order.status.toLowerCase()}`}>{order.status}</span></td>
                      <td>{order.createdAt ? new Date(order.createdAt).toLocaleString() : '—'}</td>
                      <td className={`order-pnl ${order.pnl === null ? 'neutral' : Number(order.pnl) > 0 ? 'positive' : Number(order.pnl) < 0 ? 'negative' : 'neutral'}`}>
                        {formatPnl(order.pnl, order.pnlCurrency)}
                      </td>
                      <td>{order.risk}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  )
}
