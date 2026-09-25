import { useMemo, useState } from 'react'

type ChartCandle = { time: string; open: number; high: number; low: number; close: number }
type ChartSignal = { time: string; side: 'BUY' | 'SELL'; price: number; sourceTimeframe: string; aiReason?: string; signalStrength?: number; entryThreshold?: number }
type ChartTrade = { time: string; side: 'BUY' | 'SELL'; price?: number; pnl?: string }

export type ForexChartData = {
  pair: string
  timeframe: string
  approvedTimeframe: string
  candles: ChartCandle[]
  signals: ChartSignal[]
  trades: ChartTrade[]
  indicators: { ema: number[] }
}

export class ChartIndicatorSet {
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
  const [visible, setVisible] = useState({ candles: true, close: true, ema: true, buy: true, sell: true, ai: true })
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const chart = useMemo(() => {
    const closes = data.candles.map((candle) => candle.close)
    const values = data.candles.flatMap((candle) => [candle.high, candle.low])
    const ema = data.indicators.ema.length ? data.indicators.ema : ChartIndicatorSet.ema(closes, 20)
    const min = Math.min(...values, ...ema)
    const max = Math.max(...values, ...ema)
    const scaleX = (index: number) => padding + (index / Math.max(data.candles.length - 1, 1)) * (width - padding * 2)
    const scaleY = (value: number) => height - padding - ((value - min) / Math.max(max - min, 0.00001)) * (height - padding * 2)
    const line = (points: number[]) => points.map((value, index) => `${scaleX(index)},${scaleY(value)}`).join(' ')
    const signalMarkers = data.signals.map((signal) => {
      const index = data.candles.findIndex((candle) => candle.time === signal.time)
      return index < 0 ? null : { ...signal, x: scaleX(index), y: scaleY(signal.price) }
    }).filter(Boolean) as Array<ChartSignal & { x: number; y: number }>
    const candleWidth = Math.max(3, Math.min(14, (width - padding * 2) / Math.max(data.candles.length, 1) * 0.62))
    return { line, ema, signalMarkers, scaleX, scaleY, min, max, candleWidth }
  }, [data])

  const hoveredCandle = hoveredIndex === null ? null : data.candles[hoveredIndex]
  const hoveredSignal = hoveredCandle ? data.signals.find((signal) => signal.time === hoveredCandle.time) : undefined
  const toggle = (key: keyof typeof visible) => setVisible((current) => ({ ...current, [key]: !current[key] }))

  return (
    <div className="forex-chart-shell">
      <div className="chart-meta">
        <strong>{data.pair}</strong>
        <span>{data.timeframe} view</span>
        <span>Signals: {data.approvedTimeframe} strategy</span>
        <span>{data.candles.length} candles</span>
      </div>
      <div className="forex-chart-stage">
        <svg className="forex-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.pair} price chart`} onClick={() => { if (hoveredCandle) onPriceSelect?.(hoveredCandle.close) }} onMouseLeave={() => setHoveredIndex(null)} onMouseMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect()
          const chartX = ((event.clientX - bounds.left) / bounds.width) * width
          const index = Math.round(((chartX - padding) / (width - padding * 2)) * Math.max(data.candles.length - 1, 1))
          setHoveredIndex(Math.max(0, Math.min(data.candles.length - 1, index)))
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
          {visible.ema && <polyline points={chart.line(chart.ema)} fill="none" stroke="#fbbf24" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ai && chart.signalMarkers.map((signal) => visible[signal.side === 'BUY' ? 'buy' : 'sell'] && <g key={`${signal.time}-${signal.side}`}><circle cx={signal.x} cy={signal.y} r="7" fill="none" stroke={signal.side === 'BUY' ? '#a7f3d0' : '#fecdd3'} strokeWidth="1" opacity="0.75" /><circle cx={signal.x} cy={signal.y} r="4" fill={signal.side === 'BUY' ? '#34d399' : '#fb7185'} /><text x={signal.x + 9} y={signal.y - 8} fill={signal.side === 'BUY' ? '#86efac' : '#fda4af'} fontSize="11">AI {signal.side}</text></g>)}
          {hoveredIndex !== null && <line x1={chart.scaleX(hoveredIndex)} x2={chart.scaleX(hoveredIndex)} y1={padding} y2={height - padding} stroke="#cbd5e1" strokeOpacity="0.25" strokeDasharray="3 3" />}
        </svg>
        {hoveredCandle && <div className="chart-tooltip"><strong>{new Date(hoveredCandle.time).toLocaleString()}</strong><span>O {hoveredCandle.open.toFixed(5)} · H {hoveredCandle.high.toFixed(5)}</span><span>L {hoveredCandle.low.toFixed(5)} · C {hoveredCandle.close.toFixed(5)}</span>{hoveredSignal && <><span className={hoveredSignal.side === 'BUY' ? 'tooltip-buy' : 'tooltip-sell'}>AI {hoveredSignal.side} · {hoveredSignal.sourceTimeframe}</span><span>{hoveredSignal.aiReason ?? 'AI signal'} · strength {hoveredSignal.signalStrength?.toFixed(3) ?? '-'}</span></>}</div>}
      </div>
      <div className="chart-legend"><button type="button" className={visible.candles ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('candles')}><i className="legend-candle" />Candles</button><button type="button" className={visible.close ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('close')}><i className="legend-line price" />Close</button><button type="button" className={visible.ema ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ema')}><i className="legend-line ema" />EMA 20</button><button type="button" className={visible.ai ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ai')}><i className="legend-dot buy" />AI activity</button><button type="button" className={visible.buy ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('buy')}><i className="legend-dot buy" />Buy</button><button type="button" className={visible.sell ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('sell')}><i className="legend-dot sell" />Sell</button></div>
    </div>
  )
}
