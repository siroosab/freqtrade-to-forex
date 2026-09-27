import { useMemo, useState } from 'react'

type ChartCandle = { time: string; open: number; high: number; low: number; close: number }
type ChartSignal = { time: string; side: 'BUY' | 'SELL'; price: number; sourceTimeframe: string; aiReason?: string; signalStrength?: number; entryThreshold?: number }
type ChartTrade = { time?: string; createdAt?: string; side: 'BUY' | 'SELL'; price?: number; pnl?: string }

export type ForexChartData = {
  pair: string
  timeframe: string
  approvedTimeframe: string
  candles: ChartCandle[]
  signals: ChartSignal[]
  trades: ChartTrade[]
  indicators: { ema: number[] }
}

class ChartIndicatorSet {
  static ema(values: number[], period: number) {
    if (!values.length) return []
    const multiplier = 2 / (period + 1)
    const output = [values[0]]
    for (let index = 1; index < values.length; index += 1) {
      output.push((values[index] - output[index - 1]) * multiplier + output[index - 1])
    }
    return output
  }
}

export function ForexChart({ data, onPriceSelect }: { data: ForexChartData; onPriceSelect?: (price: number) => void }) {
  const width = 960
  const height = 340
  const padding = 28
  const [visible, setVisible] = useState({ candles: true, close: true, ema20: true, ema50: true, ema100: true, buy: true, sell: true, ai: true, trades: true })
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [zoomLevel, setZoomLevel] = useState(0)
  const visibleCount = Math.max(1, Math.ceil(data.candles.length / (1.5 ** zoomLevel)))
  const visibleStartIndex = Math.max(0, data.candles.length - visibleCount)
  const visibleEndIndex = Math.max(visibleStartIndex, data.candles.length - 1)
  const chart = useMemo(() => {
    const closes = data.candles.map((candle) => candle.close)
    const visibleCandles = data.candles.slice(visibleStartIndex, visibleEndIndex + 1)
    const values = visibleCandles.flatMap((candle) => [candle.high, candle.low])
    const ema20 = data.indicators.ema.length === closes.length ? data.indicators.ema : ChartIndicatorSet.ema(closes, 20)
    const ema50 = ChartIndicatorSet.ema(closes, 50)
    const ema100 = ChartIndicatorSet.ema(closes, 100)
    const scaleX = (index: number) => padding + ((index - visibleStartIndex) / Math.max(visibleEndIndex - visibleStartIndex, 1)) * (width - padding * 2)
    const startTime = Date.parse(visibleCandles[0]?.time ?? '')
    const endTime = Date.parse(visibleCandles[visibleCandles.length - 1]?.time ?? '')
    const visibleSignals = data.signals.filter((signal) => {
      const time = Date.parse(signal.time)
      return time >= startTime && time <= endTime
    })
    const visibleTrades = data.trades.filter((trade) => {
      const time = Date.parse(trade.time ?? trade.createdAt ?? '')
      return time >= startTime && time <= endTime
    })
    const allPrices = [...values, ...ema20.slice(visibleStartIndex, visibleEndIndex + 1), ...ema50.slice(visibleStartIndex, visibleEndIndex + 1), ...ema100.slice(visibleStartIndex, visibleEndIndex + 1), ...visibleSignals.map((signal) => signal.price), ...visibleTrades.map((trade) => trade.price).filter((price): price is number => typeof price === 'number')]
    const min = Math.min(...allPrices)
    const max = Math.max(...allPrices)
    const scaleY = (value: number) => height - padding - ((value - min) / Math.max(max - min, 0.00001)) * (height - padding * 2)
    const line = (points: number[]) => points.map((value, index) => `${scaleX(index)},${scaleY(value)}`).join(' ')
    const nearestCandleIndex = (time: string) => {
      const target = Date.parse(time)
      if (!Number.isFinite(target) || !data.candles.length) return -1
      const first = Date.parse(data.candles[0].time)
      const last = Date.parse(data.candles[data.candles.length - 1].time)
      if (target < first || target > last) return -1
      let nearestIndex = 0
      let nearestDistance = Number.POSITIVE_INFINITY
      data.candles.forEach((candle, index) => {
        const distance = Math.abs(Date.parse(candle.time) - target)
        if (distance < nearestDistance) {
          nearestIndex = index
          nearestDistance = distance
        }
      })
      return nearestIndex
    }
    const signalMarkers = data.signals.map((signal) => {
      const index = data.candles.findIndex((candle) => candle.time === signal.time)
      return index < 0 ? null : { ...signal, x: scaleX(index), y: scaleY(signal.price) }
    }).filter(Boolean) as Array<ChartSignal & { x: number; y: number }>
    const tradeMarkers = data.trades.map((trade) => {
      const time = trade.time ?? trade.createdAt
      if (!time || (trade.side !== 'BUY' && trade.side !== 'SELL')) return null
      const index = nearestCandleIndex(time)
      if (index < 0) return null
      return { ...trade, x: scaleX(index), y: scaleY(trade.price ?? data.candles[index].close) }
    }).filter(Boolean) as Array<ChartTrade & { x: number; y: number }>
    const candleWidth = Math.max(3, Math.min(14, (width - padding * 2) / Math.max(visibleCount, 1) * 0.62))
    return { line, ema20, ema50, ema100, signalMarkers, tradeMarkers, scaleX, scaleY, min, max, candleWidth }
  }, [data, visibleCount, visibleEndIndex, visibleStartIndex])

  const hoveredCandle = hoveredIndex === null ? null : data.candles[hoveredIndex]
  const hoveredSignal = hoveredCandle ? data.signals.find((signal) => signal.time === hoveredCandle.time) : undefined
  const toggle = (key: keyof typeof visible) => setVisible((current) => ({ ...current, [key]: !current[key] }))
  const zoom = (direction: -1 | 1) => {
    setHoveredIndex(null)
    setZoomLevel((current) => Math.max(0, Math.min(4, current + direction)))
  }

  return (
    <div className="forex-chart-shell">
      <div className="chart-meta">
        <strong>{data.pair}</strong>
        <span>{data.timeframe} view</span>
        <span>Signals: {data.approvedTimeframe} strategy</span>
        <span>{data.candles.length} candles</span>
        <div className="chart-zoom-controls" aria-label="Chart zoom controls">
          <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => zoom(-1)} disabled={zoomLevel === 0}>-</button>
          <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => zoom(1)} disabled={zoomLevel === 4 || data.candles.length <= 1}>+</button>
          <button type="button" className="chart-zoom-reset" onClick={() => { setZoomLevel(0); setHoveredIndex(null) }} disabled={zoomLevel === 0}>Reset zoom</button>
        </div>
      </div>
      <div className="forex-chart-stage">
        <svg className="forex-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.pair} price chart`} onClick={() => { if (hoveredCandle) onPriceSelect?.(hoveredCandle.close) }} onMouseLeave={() => setHoveredIndex(null)} onMouseMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect()
          const chartX = ((event.clientX - bounds.left) / bounds.width) * width
          const index = visibleStartIndex + Math.round(((chartX - padding) / (width - padding * 2)) * Math.max(visibleEndIndex - visibleStartIndex, 1))
          setHoveredIndex(Math.max(visibleStartIndex, Math.min(visibleEndIndex, index)))
        }}>
          {visible.candles && data.candles.map((candle, index) => {
            const x = chart.scaleX(index)
            const openY = chart.scaleY(candle.open)
            const closeY = chart.scaleY(candle.close)
            const highY = chart.scaleY(candle.high)
            const lowY = chart.scaleY(candle.low)
            const bullish = candle.close >= candle.open
            return <g key={candle.time} className="chart-candle"><line x1={x} x2={x} y1={highY} y2={lowY} stroke={bullish ? '#34d399' : '#fb7185'} strokeWidth="1" /><rect x={x - chart.candleWidth / 2} y={Math.min(openY, closeY)} width={chart.candleWidth} height={Math.max(1, Math.abs(closeY - openY))} fill={bullish ? '#34d399' : '#fb7185'} opacity="0.88" /></g>
          })}
          {visible.close && <polyline points={chart.line(data.candles.map((candle) => candle.close))} fill="none" stroke="#38bdf8" strokeWidth="1.5" />}
          {visible.ema20 && <polyline points={chart.line(chart.ema20)} fill="none" stroke="#fbbf24" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ema50 && <polyline points={chart.line(chart.ema50)} fill="none" stroke="#c084fc" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ema100 && <polyline points={chart.line(chart.ema100)} fill="none" stroke="#f97316" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ai && chart.signalMarkers.map((signal) => visible[signal.side === 'BUY' ? 'buy' : 'sell'] && <g key={`${signal.time}-${signal.side}`}><circle cx={signal.x} cy={signal.y} r="7" fill="none" stroke={signal.side === 'BUY' ? '#a7f3d0' : '#fecdd3'} strokeWidth="1" opacity="0.75" /><circle cx={signal.x} cy={signal.y} r="4" fill={signal.side === 'BUY' ? '#34d399' : '#fb7185'} /><text x={signal.x + 9} y={signal.y - 8} fill={signal.side === 'BUY' ? '#86efac' : '#fda4af'} fontSize="11">AI {signal.side}</text></g>)}
          {visible.trades && chart.tradeMarkers.map((trade, index) => <g key={`${trade.time ?? trade.createdAt}-${trade.side}-${index}`}><polygon points={`${trade.x},${trade.y - 7} ${trade.x + 6},${trade.y} ${trade.x},${trade.y + 7} ${trade.x - 6},${trade.y}`} fill={trade.side === 'BUY' ? '#34d399' : '#fb7185'} stroke="#f8fafc" strokeWidth="1" /><text x={trade.x + 9} y={trade.y + 14} fill={trade.side === 'BUY' ? '#86efac' : '#fda4af'} fontSize="11">Trade {trade.side}</text></g>)}
          {hoveredIndex !== null && <line x1={chart.scaleX(hoveredIndex)} x2={chart.scaleX(hoveredIndex)} y1={padding} y2={height - padding} stroke="#cbd5e1" strokeOpacity="0.25" strokeDasharray="3 3" />}
        </svg>
        {hoveredCandle && <div className="chart-tooltip"><strong>{new Date(hoveredCandle.time).toLocaleString()}</strong><span>O {hoveredCandle.open.toFixed(5)} · H {hoveredCandle.high.toFixed(5)}</span><span>L {hoveredCandle.low.toFixed(5)} · C {hoveredCandle.close.toFixed(5)}</span>{hoveredSignal && <><span className={hoveredSignal.side === 'BUY' ? 'tooltip-buy' : 'tooltip-sell'}>AI {hoveredSignal.side} · {hoveredSignal.sourceTimeframe}</span><span>{hoveredSignal.aiReason ?? 'AI signal'} · strength {hoveredSignal.signalStrength?.toFixed(3) ?? '-'}</span></>}</div>}
      </div>
      <div className="chart-legend"><button type="button" className={visible.candles ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('candles')}><i className="legend-candle" />Candles</button><button type="button" className={visible.close ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('close')}><i className="legend-line price" />Close</button><button type="button" className={visible.ema20 ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ema20')}><i className="legend-line ema" />EMA 20</button><button type="button" className={visible.ema50 ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ema50')}><i className="legend-line ema50" />EMA 50</button><button type="button" className={visible.ema100 ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ema100')}><i className="legend-line ema100" />EMA 100</button><button type="button" className={visible.trades ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('trades')}><i className="legend-dot trade" />Trades</button><button type="button" className={visible.ai ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ai')}><i className="legend-dot buy" />AI activity</button><button type="button" className={visible.buy ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('buy')}><i className="legend-dot buy" />Buy</button><button type="button" className={visible.sell ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('sell')}><i className="legend-dot sell" />Sell</button></div>
    </div>
  )
}
