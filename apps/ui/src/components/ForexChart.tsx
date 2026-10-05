import { useMemo, useRef, useState } from 'react'

type ChartCandle = { time: string; open: number; high: number; low: number; close: number }
type ChartSignal = {
  time: string
  side: 'BUY' | 'SELL'
  price: number
  sourceTimeframe: string
  roiTargetPrice?: number
  roiPercent?: number
}
type ChartTrade = { time?: string; createdAt?: string; side: 'BUY' | 'SELL'; price?: number; pnl?: string; source?: string; markerType?: 'entry' | 'exit' }

export type ForexChartData = {
  pair: string
  timeframe: string
  approvedTimeframe: string
  approvedStrategy: string
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
  const plotRight = width - 78
  const plotWidth = plotRight - padding
  const [visible, setVisible] = useState({ candles: true, close: true, ema20: true, ema50: true, ema100: true, buy: true, sell: true, signals: true, roiTargets: true, trades: true })
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [zoomLevel, setZoomLevel] = useState(0)
  const dataKey = `${data.pair}:${data.timeframe}`
  const [reference, setReference] = useState(() => ({
    key: dataKey,
    price: data.candles[Math.floor(data.candles.length / 2)]?.close ?? data.candles[0]?.close ?? 1,
  }))
  const isReferenceDragging = useRef(false)
  const visibleReferencePrice = reference.key === dataKey
    ? reference.price
    : data.candles[Math.floor(data.candles.length / 2)]?.close ?? data.candles[0]?.close ?? 1
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
    const scaleX = (index: number) => padding + ((index - visibleStartIndex) / Math.max(visibleEndIndex - visibleStartIndex, 1)) * plotWidth
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
    const allPrices = [...values, ...ema20.slice(visibleStartIndex, visibleEndIndex + 1), ...ema50.slice(visibleStartIndex, visibleEndIndex + 1), ...ema100.slice(visibleStartIndex, visibleEndIndex + 1), ...visibleSignals.flatMap((signal) => [signal.price, signal.roiTargetPrice].filter((price): price is number => typeof price === 'number' && Number.isFinite(price))), ...visibleTrades.map((trade) => trade.price).filter((price): price is number => typeof price === 'number')]
    const min = Math.min(...allPrices)
    const max = Math.max(...allPrices)
    const scaleY = (value: number) => height - padding - ((value - min) / Math.max(max - min, 0.00001)) * (height - padding * 2)
    const priceAtY = (y: number) => max - ((y - padding) / (height - padding * 2)) * (max - min)
    const percentageTicks = Array.from({ length: 7 }, (_, index) => {
      const y = padding + (index / 6) * (height - padding * 2)
      return { y, percent: ((priceAtY(y) / visibleReferencePrice) - 1) * 100 }
    })
    const line = (points: number[], startIndex = 0) => points.map((value, index) => `${scaleX(index + startIndex)},${scaleY(value)}`).join(' ')
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
    const signalMarkers = visibleSignals.map((signal) => {
      const index = data.candles.findIndex((candle) => candle.time === signal.time)
      return index < 0 ? null : { ...signal, x: scaleX(index), y: scaleY(signal.price) }
    }).filter(Boolean) as Array<ChartSignal & { x: number; y: number }>
    const roiMarkers = signalMarkers.flatMap((signal) =>
      typeof signal.roiTargetPrice === 'number' && Number.isFinite(signal.roiTargetPrice) && typeof signal.roiPercent === 'number' && Number.isFinite(signal.roiPercent)
        ? [{ ...signal, roiY: scaleY(signal.roiTargetPrice) }]
        : [],
    )
    const tradeMarkers = data.trades.map((trade) => {
      const time = trade.time ?? trade.createdAt
      if (!time || (trade.side !== 'BUY' && trade.side !== 'SELL')) return null
      const index = nearestCandleIndex(time)
      if (index < visibleStartIndex || index > visibleEndIndex) return null
      return { ...trade, x: scaleX(index), y: scaleY(trade.price ?? data.candles[index].close) }
    }).filter(Boolean) as Array<ChartTrade & { x: number; y: number }>
    const candleWidth = Math.max(3, Math.min(14, (width - padding * 2) / Math.max(visibleCount, 1) * 0.62))
    return { line, ema20, ema50, ema100, signalMarkers, roiMarkers, tradeMarkers, scaleX, scaleY, priceAtY, percentageTicks, min, max, candleWidth }
  }, [data, plotWidth, visibleCount, visibleEndIndex, visibleReferencePrice, visibleStartIndex])

  const hoveredCandle = hoveredIndex === null ? null : data.candles[hoveredIndex]
  const hoveredSignal = hoveredCandle ? data.signals.find((signal) => signal.time === hoveredCandle.time) : undefined
  const toggle = (key: keyof typeof visible) => setVisible((current) => ({ ...current, [key]: !current[key] }))
  const zoom = (direction: -1 | 1) => {
    setHoveredIndex(null)
    setZoomLevel((current) => Math.max(0, Math.min(4, current + direction)))
  }
  const pointerPosition = (svg: SVGSVGElement, clientX: number, clientY: number) => {
    const point = svg.createSVGPoint()
    point.x = clientX
    point.y = clientY
    const transform = svg.getScreenCTM()?.inverse()
    return transform ? point.matrixTransform(transform) : point
  }
  const moveReferenceToPointer = (event: React.PointerEvent<SVGSVGElement>) => {
    const position = pointerPosition(event.currentTarget, event.clientX, event.clientY)
    const y = Math.max(padding, Math.min(height - padding, position.y))
    setReference({ key: dataKey, price: chart.priceAtY(y) })
  }

  return (
    <div className="forex-chart-shell">
      <div className="chart-meta">
        <strong>{data.pair}</strong>
        <span>{data.timeframe} view</span>
        <span className="chart-approved-scope" title="Approved strategy and signal timeframe">{data.approvedStrategy} · {data.approvedTimeframe}</span>
        <span>{data.candles.length} candles</span>
        <div className="chart-zoom-controls" aria-label="Chart zoom controls">
          <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => zoom(-1)} disabled={zoomLevel === 0}>-</button>
          <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => zoom(1)} disabled={zoomLevel === 4 || data.candles.length <= 1}>+</button>
          <button type="button" className="chart-zoom-reset" onClick={() => { setZoomLevel(0); setHoveredIndex(null) }} disabled={zoomLevel === 0}>Reset zoom</button>
        </div>
      </div>
      <div className="forex-chart-stage">
        <svg className="forex-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.pair} price chart with percentage scale`} onClick={() => { if (!isReferenceDragging.current && hoveredCandle) onPriceSelect?.(hoveredCandle.close) }} onPointerMove={(event) => {
          if (isReferenceDragging.current) {
            moveReferenceToPointer(event)
            return
          }
          const chartX = pointerPosition(event.currentTarget, event.clientX, event.clientY).x
          const index = visibleStartIndex + Math.round(((chartX - padding) / plotWidth) * Math.max(visibleEndIndex - visibleStartIndex, 1))
          setHoveredIndex(Math.max(visibleStartIndex, Math.min(visibleEndIndex, index)))
        }} onPointerLeave={() => { if (!isReferenceDragging.current) setHoveredIndex(null) }}>
          {visible.candles && data.candles.slice(visibleStartIndex, visibleEndIndex + 1).map((candle, offset) => {
            const index = visibleStartIndex + offset
            const x = chart.scaleX(index)
            const openY = chart.scaleY(candle.open)
            const closeY = chart.scaleY(candle.close)
            const highY = chart.scaleY(candle.high)
            const lowY = chart.scaleY(candle.low)
            const bullish = candle.close >= candle.open
            return <g key={candle.time} className="chart-candle"><line x1={x} x2={x} y1={highY} y2={lowY} stroke={bullish ? '#34d399' : '#fb7185'} strokeWidth="1" /><rect x={x - chart.candleWidth / 2} y={Math.min(openY, closeY)} width={chart.candleWidth} height={Math.max(1, Math.abs(closeY - openY))} fill={bullish ? '#34d399' : '#fb7185'} opacity="0.88" /></g>
          })}
          {visible.close && <polyline points={chart.line(data.candles.slice(visibleStartIndex, visibleEndIndex + 1).map((candle) => candle.close), visibleStartIndex)} fill="none" stroke="#38bdf8" strokeWidth="1.5" />}
          {visible.ema20 && <polyline points={chart.line(chart.ema20.slice(visibleStartIndex, visibleEndIndex + 1), visibleStartIndex)} fill="none" stroke="#fbbf24" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ema50 && <polyline points={chart.line(chart.ema50.slice(visibleStartIndex, visibleEndIndex + 1), visibleStartIndex)} fill="none" stroke="#c084fc" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ema100 && <polyline points={chart.line(chart.ema100.slice(visibleStartIndex, visibleEndIndex + 1), visibleStartIndex)} fill="none" stroke="#f97316" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.signals && visible.roiTargets && chart.roiMarkers.map((signal, index) => visible[signal.side === 'BUY' ? 'buy' : 'sell'] && <g key={`${signal.time}-${signal.side}-roi-${index}`}><line x1={signal.x} x2={signal.x} y1={signal.y} y2={signal.roiY} stroke="#fbbf24" strokeWidth="1.5" strokeDasharray="3 3" opacity="0.8" /><polygon points={`${signal.x},${signal.roiY - 6} ${signal.x + 6},${signal.roiY} ${signal.x},${signal.roiY + 6} ${signal.x - 6},${signal.roiY}`} fill="#0f172a" stroke="#fbbf24" strokeWidth="2"><title>Projected ROI target +{signal.roiPercent?.toFixed(2)}%; not an actual or executed exit</title></polygon><text x={signal.x + 9} y={signal.roiY - 5} fill="#fcd34d" fontSize="10">ROI +{signal.roiPercent?.toFixed(2)}% · projected</text></g>)}
          {visible.signals && chart.signalMarkers.map((signal) => visible[signal.side === 'BUY' ? 'buy' : 'sell'] && <g key={`${signal.time}-${signal.side}`}><circle cx={signal.x} cy={signal.y} r="7" fill="none" stroke={signal.side === 'BUY' ? '#a7f3d0' : '#fecdd3'} strokeWidth="1" opacity="0.75" /><circle cx={signal.x} cy={signal.y} r="4" fill={signal.side === 'BUY' ? '#34d399' : '#fb7185'} /><text x={signal.x + 9} y={signal.y - 8} fill={signal.side === 'BUY' ? '#86efac' : '#fda4af'} fontSize="11">Signal {signal.side}</text></g>)}
          {visible.trades && chart.tradeMarkers.map((trade, index) => {
            const manual = trade.source === 'manual'
            const color = manual ? '#facc15' : trade.side === 'BUY' ? '#34d399' : '#fb7185'
            const label = manual ? `MANUAL ${trade.side} ${trade.markerType === 'exit' ? 'EXIT' : 'ENTRY'}` : `Strategy ${trade.side}`
            return <g key={`${trade.time ?? trade.createdAt}-${trade.side}-${index}`}><polygon points={`${trade.x},${trade.y - 7} ${trade.x + 6},${trade.y} ${trade.x},${trade.y + 7} ${trade.x - 6},${trade.y}`} fill={manual ? '#0f172a' : color} stroke={color} strokeWidth={manual ? '2' : '1'} strokeDasharray={manual ? '2 1' : undefined} /><text x={trade.x + 9} y={trade.y + 14} fill={color} fontSize="11">{label}{manual && trade.markerType === 'exit' && trade.pnl ? ` ${trade.pnl}` : ''}</text></g>
          })}
          <line x1={padding} x2={plotRight} y1={chart.scaleY(visibleReferencePrice)} y2={chart.scaleY(visibleReferencePrice)} className="chart-zero-line" />
          <g className="chart-zero-handle" role="slider" aria-label="Movable zero price reference" aria-valuemin={chart.min} aria-valuemax={chart.max} aria-valuenow={visibleReferencePrice} tabIndex={0} onPointerDown={(event) => {
            event.preventDefault()
            event.stopPropagation()
            isReferenceDragging.current = true
            event.currentTarget.setPointerCapture(event.pointerId)
            const svg = event.currentTarget.ownerSVGElement
            if (svg) {
              const point = pointerPosition(svg, event.clientX, event.clientY)
              const y = Math.max(padding, Math.min(height - padding, point.y))
              setReference({ key: dataKey, price: chart.priceAtY(y) })
            }
          }} onPointerUp={(event) => {
            isReferenceDragging.current = false
            event.stopPropagation()
            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
          }} onPointerCancel={() => { isReferenceDragging.current = false }} onKeyDown={(event) => {
            if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
            event.preventDefault()
            const step = (chart.max - chart.min) / 100
            setReference({ key: dataKey, price: visibleReferencePrice + (event.key === 'ArrowUp' ? step : -step) })
          }} onClick={(event) => event.stopPropagation()}>
            <rect x={padding} y={chart.scaleY(visibleReferencePrice) - 10} width={plotWidth} height="20" fill="transparent" />
            <rect x={plotRight - 5} y={chart.scaleY(visibleReferencePrice) - 8} width="10" height="16" rx="3" className="chart-zero-grip" />
            <text x={plotRight + 9} y={chart.scaleY(visibleReferencePrice) - 5} className="chart-zero-label">0.00%</text>
            <text x={plotRight + 9} y={chart.scaleY(visibleReferencePrice) + 9} className="chart-zero-price">{visibleReferencePrice.toFixed(5)}</text>
          </g>
          <line x1={plotRight} x2={plotRight} y1={padding} y2={height - padding} className="chart-percent-axis" />
          {chart.percentageTicks.map((tick, index) => <g key={index} className="chart-percent-tick"><line x1={plotRight - 4} x2={plotRight + 3} y1={tick.y} y2={tick.y} /><text x={plotRight + 9} y={tick.y + 3}>{tick.percent > 0 ? '+' : ''}{tick.percent.toFixed(2)}%</text></g>)}
          {hoveredIndex !== null && <line x1={chart.scaleX(hoveredIndex)} x2={chart.scaleX(hoveredIndex)} y1={padding} y2={height - padding} stroke="#cbd5e1" strokeOpacity="0.25" strokeDasharray="3 3" />}
        </svg>
        {hoveredCandle && <div className="chart-tooltip"><strong>{new Date(hoveredCandle.time).toLocaleString()}</strong><span>O {hoveredCandle.open.toFixed(5)} · H {hoveredCandle.high.toFixed(5)}</span><span>L {hoveredCandle.low.toFixed(5)} · C {hoveredCandle.close.toFixed(5)}</span><span>Change from zero {((hoveredCandle.close / visibleReferencePrice - 1) * 100) > 0 ? '+' : ''}{((hoveredCandle.close / visibleReferencePrice - 1) * 100).toFixed(2)}%</span>{hoveredSignal && <span className={hoveredSignal.side === 'BUY' ? 'tooltip-buy' : 'tooltip-sell'}>Strategy {hoveredSignal.side} · {hoveredSignal.sourceTimeframe}</span>}</div>}
      </div>
      <div className="chart-legend"><button type="button" className={visible.candles ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('candles')}><i className="legend-candle" />Candles</button><button type="button" className={visible.close ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('close')}><i className="legend-line price" />Close</button><button type="button" className={visible.ema20 ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ema20')}><i className="legend-line ema" />EMA 20</button><button type="button" className={visible.ema50 ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ema50')}><i className="legend-line ema50" />EMA 50</button><button type="button" className={visible.ema100 ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('ema100')}><i className="legend-line ema100" />EMA 100</button><button type="button" className={visible.trades ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('trades')}><i className="legend-dot trade" />Trades</button><button type="button" className={visible.signals ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('signals')}><i className="legend-dot buy" />Signals</button><button type="button" className={visible.roiTargets ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('roiTargets')}><i className="legend-dot roi-target" />ROI targets · projected only</button><button type="button" className={visible.buy ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('buy')}><i className="legend-dot buy" />Buy</button><button type="button" className={visible.sell ? 'legend-toggle active' : 'legend-toggle'} onClick={() => toggle('sell')}><i className="legend-dot sell" />Sell</button></div>
    </div>
  )
}
