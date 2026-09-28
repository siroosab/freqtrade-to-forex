import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { getOrders, getOrdersChart } from '../api/mockApi'
import { ForexChart } from '../components/ForexChart'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount } from '../components/chartOptions'
import { useUiStore } from '../store/useUiStore'

export function OrdersPage() {
  const { data } = useQuery({ queryKey: ['orders'], queryFn: getOrders })
  const availablePairs = useUiStore((state) => state.selectedInstruments)
  const [pair, setPair] = useState(availablePairs[0] ?? 'EUR/USD')
  useEffect(() => {
    if (!availablePairs.length) return
    setPair((current) => (availablePairs.includes(current) ? current : availablePairs[0]))
  }, [availablePairs])
  const [timeframe, setTimeframe] = useState('M15')
  const [countMultiplier, setCountMultiplier] = useState(1)
  const chartQuery = useQuery({ queryKey: ['orders-chart', pair, timeframe, countMultiplier], queryFn: () => getOrdersChart(pair, timeframe, chartCandleCount(countMultiplier)), refetchInterval: 30000 })
  const liveOrders = useUiStore((state) => state.ordersFeed)

  const orders = liveOrders ?? data

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Orders</p>
          <h2>Execution log</h2>
        </div>
      </header>

      <section className="panel page-panel">
        <div className="panel-header compact">
          <div><p className="eyebrow">Market context</p><h3>Signals and trade map</h3></div>
          <div className="chart-controls"><select aria-label="Chart pair" value={pair} onChange={(event) => setPair(event.target.value)}>{(availablePairs.length ? availablePairs : ['EUR/USD','GBP/USD','USD/JPY']).map((item) => <option key={item} value={item}>{item}</option>)}</select><ChartDataControls timeframe={timeframe} countMultiplier={countMultiplier} onTimeframeChange={setTimeframe} onCountMultiplierChange={setCountMultiplier} /></div>
        </div>
        {chartQuery.isLoading && <p>Loading broker candles and approved strategy signals…</p>}
        {chartQuery.isError && <p role="alert">{chartQuery.error instanceof Error ? chartQuery.error.message : 'Chart data unavailable from the broker API.'}</p>}
        {chartQuery.data && <ForexChart data={chartQuery.data} />}
      </section>

      <section className="panel page-panel">
        <table className="positions-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Volume</th>
              <th>Status</th>
              <th>Created</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {orders?.map((order) => (
              <tr key={order.id}>
                <td>{order.id}</td>
                <td>{order.symbol}</td>
                <td>{order.side}</td>
                <td>{order.volume}</td>
                <td>{order.status}</td>
                <td>{new Date(order.createdAt).toLocaleTimeString()}</td>
                <td>{order.risk}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}
