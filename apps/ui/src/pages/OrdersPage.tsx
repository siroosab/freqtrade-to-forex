import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getOrders, getOrdersChart } from '../api/mockApi'
import { ForexChart } from '../components/ForexChart'
import { useUiStore } from '../store/useUiStore'

export function OrdersPage() {
  const { data } = useQuery({ queryKey: ['orders'], queryFn: getOrders })
  const [pair, setPair] = useState('EUR/USD')
  const [timeframe, setTimeframe] = useState('M15')
  const chartQuery = useQuery({ queryKey: ['orders-chart', pair, timeframe], queryFn: () => getOrdersChart(pair, timeframe), refetchInterval: 30000 })
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
          <div className="chart-controls"><select value={pair} onChange={(event) => setPair(event.target.value)}><option>EUR/USD</option><option>GBP/USD</option><option>USD/JPY</option></select><select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}><option>M5</option><option>M15</option><option>H1</option></select></div>
        </div>
        {chartQuery.isLoading && <p>Loading broker candles and approved strategy signals…</p>}
        {chartQuery.isError && <p>Chart data unavailable from the broker API.</p>}
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
