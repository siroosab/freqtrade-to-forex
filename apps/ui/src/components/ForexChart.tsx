import { useMemo, useRef, useState } from 'react'

const MAX_ZOOM_LEVEL = 8

type ChartCandle = { time: string; open: number; high: number; low: number; close: number }
type ChartSignalType = 'entry_long' | 'entry_short' | 'exit_long' | 'exit_short'
type ChartSignal = {
  time: string
  side: 'BUY' | 'SELL'
  price: number
  sourceTimeframe: string
  signalType?: ChartSignalType
}
type ChartTrade = { time?: string; createdAt?: string; side: 'BUY' | 'SELL'; price?: number; pnl?: string; source?: string; markerType?: 'entry' | 'exit' }
export type StrategyChartIndicator = { panel: 'overlay' | 'lower'; values: Array<number | null> }

export type ForexChartData = {
  pair: string
  timeframe: string
  approvedTimeframe: string
  approvedStrategy: string
  candles: ChartCandle[]
  signals: ChartSignal[]
  trades: ChartTrade[]
  indicators: { ema: number[]; strategy?: Record<string, StrategyChartIndicator> }
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

const STRATEGY_INDICATOR_COLORS = ['#22d3ee', '#f472b6', '#a3e635', '#fb923c', '#818cf8', '#2dd4bf', '#e879f9']

function indicatorPath(
  values: Array<number | null>,
  startIndex: number,
  scaleX: (index: number) => number,
  scaleY: (value: number) => number,
) {
  let path = ''
  let segmentOpen = false
  values.forEach((value, offset) => {
    if (value === null || !Number.isFinite(value)) {
      segmentOpen = false
      return
    }
    path += `${segmentOpen ? 'L' : 'M'}${scaleX(startIndex + offset)},${scaleY(value)} `
    segmentOpen = true
  })
  return path
}

export function ForexChart({
  data,
  onPriceSelect,
  selectedIndicators = [],
  large = false,
}: {
  data: ForexChartData
  onPriceSelect?: (price: number) => void
  selectedIndicators?: string[]
  large?: boolean
}) {
  const width = 960
  const height = large ? 520 : 340
  const padding = 28
  const plotRight = width - 78
  const plotWidth = plotRight - padding
  const [visible, setVisible] = useState({ candles: true, close: false, ema20: false, ema50: false, ema100: false, buy: false, sell: false, signals: false, exitLong: false, exitShort: false, trades: false })
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [zoomLevel, setZoomLevel] = useState(0)
  const [panOffset, setPanOffset] = useState(0)
  const [isPanning, setIsPanning] = useState(false)
  const [panelHeights, setPanelHeights] = useState<Record<string, number>>({})
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, boolean>>({})
  const dataKey = `${data.pair}:${data.timeframe}`
  const [reference, setReference] = useState(() => ({
    key: dataKey,
    price: data.candles[Math.floor(data.candles.length / 2)]?.close ?? data.candles[0]?.close ?? 1,
  }))
  const isReferenceDragging = useRef(false)
  const panStart = useRef<{ pointerId: number; x: number; offset: number; moved: boolean } | null>(null)
  const panelResizeStart = useRef<{ name: string; pointerId: number; y: number; height: number } | null>(null)
  const suppressClick = useRef(false)
  const visibleReferencePrice = reference.key === dataKey
    ? reference.price
    : data.candles[Math.floor(data.candles.length / 2)]?.close ?? data.candles[0]?.close ?? 1
  const visibleCount = Math.max(1, Math.ceil(data.candles.length / (1.5 ** zoomLevel)))
  const maxPanOffset = Math.max(0, data.candles.length - visibleCount)
  const visibleStartIndex = Math.max(0, data.candles.length - visibleCount - Math.min(panOffset, maxPanOffset))
  const visibleEndIndex = Math.max(visibleStartIndex, Math.min(data.candles.length - 1, visibleStartIndex + visibleCount - 1))
  const chart = useMemo(() => {
    const closes = data.candles.map((candle) => candle.close)
    const visibleCandles = data.candles.slice(visibleStartIndex, visibleEndIndex + 1)
    const values = visibleCandles.flatMap((candle) => [candle.high, candle.low])
    const ema20 = data.indicators.ema.length === closes.length ? data.indicators.ema : ChartIndicatorSet.ema(closes, 20)
    const ema50 = ChartIndicatorSet.ema(closes, 50)
    const ema100 = ChartIndicatorSet.ema(closes, 100)
    const selectedSeries = selectedIndicators
      .map((name) => ({ name, ...data.indicators.strategy?.[name] }))
      .filter((indicator): indicator is { name: string; panel: 'overlay' | 'lower'; values: Array<number | null> } => Boolean(indicator.panel && indicator.values))
    const overlaySeries = selectedSeries.filter((indicator) => indicator.panel === 'overlay')
    const lowerSeries = selectedSeries.filter((indicator) => indicator.panel === 'lower')
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
    const visibleEntrySignals = visibleSignals.filter((signal) => {
      const signalType = signal.signalType ?? (signal.side === 'BUY' ? 'entry_long' : 'entry_short')
      return signalType.startsWith('entry_')
        && (visible.signals || visible[signal.side === 'BUY' ? 'buy' : 'sell'])
    })
    const visibleExitSignals = visibleSignals.filter((signal) => {
      const signalType = signal.signalType
      return signalType === 'exit_long' ? visible.exitLong : signalType === 'exit_short' && visible.exitShort
    })
    const allPrices = [
      ...values,
      ...(visible.ema20 ? ema20.slice(visibleStartIndex, visibleEndIndex + 1) : []),
      ...(visible.ema50 ? ema50.slice(visibleStartIndex, visibleEndIndex + 1) : []),
      ...(visible.ema100 ? ema100.slice(visibleStartIndex, visibleEndIndex + 1) : []),
      ...overlaySeries.flatMap((indicator) => indicator.values.slice(visibleStartIndex, visibleEndIndex + 1).filter((value): value is number => value !== null && Number.isFinite(value))),
      ...visibleEntrySignals.map((signal) => signal.price),
      ...visibleExitSignals.map((signal) => signal.price),
      ...(visible.trades ? visibleTrades.map((trade) => trade.price).filter((price): price is number => typeof price === 'number') : []),
    ]
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
    const tradeMarkers = data.trades.map((trade) => {
      const time = trade.time ?? trade.createdAt
      if (!time || (trade.side !== 'BUY' && trade.side !== 'SELL')) return null
      const index = nearestCandleIndex(time)
      if (index < visibleStartIndex || index > visibleEndIndex) return null
      return { ...trade, x: scaleX(index), y: scaleY(trade.price ?? data.candles[index].close) }
    }).filter(Boolean) as Array<ChartTrade & { x: number; y: number }>
    const candleWidth = Math.max(3, Math.min(16, plotWidth / Math.max(visibleCount, 1) * 0.74))
    const lowerPanels = lowerSeries.flatMap((indicator, panelIndex) => {
      const values = indicator.values.slice(visibleStartIndex, visibleEndIndex + 1)
      const finite = values.filter((value): value is number => value !== null && Number.isFinite(value))
      if (!finite.length) return []
      const isRsi = /^rsi(?:_|$)/i.test(indicator.name)
      const dataMin = Math.min(...finite)
      const dataMax = Math.max(...finite)
      const panelPadding = Math.max((dataMax - dataMin) * 0.12, Math.abs(dataMax) * 0.00001, 0.000001)
      const panelMin = isRsi ? 0 : dataMin - panelPadding
      const panelMax = isRsi ? 100 : dataMax + panelPadding
      const scalePanelY = (value: number) => 104 - ((value - panelMin) / Math.max(panelMax - panelMin, 0.000001)) * 80
      const path = indicatorPath(values, visibleStartIndex, scaleX, scalePanelY)
      const selectedIndex = selectedIndicators.indexOf(indicator.name)
      return [{
        name: indicator.name,
        color: STRATEGY_INDICATOR_COLORS[(selectedIndex < 0 ? panelIndex : selectedIndex) % STRATEGY_INDICATOR_COLORS.length],
        path,
        scalePanelY,
        isRsi,
      }]
    })
    return { line, ema20, ema50, ema100, signalMarkers, tradeMarkers, scaleX, scaleY, priceAtY, percentageTicks, min, max, candleWidth, overlaySeries, lowerPanels }
  }, [data, height, plotWidth, selectedIndicators, visible, visibleCount, visibleEndIndex, visibleReferencePrice, visibleStartIndex])

  const hoveredCandle = hoveredIndex === null ? null : data.candles[hoveredIndex]
  const hoveredSignal = hoveredCandle ? data.signals.find((signal) => signal.time === hoveredCandle.time) : undefined
  const hoveredIndicatorValues = hoveredIndex === null
    ? []
    : selectedIndicators.flatMap((name) => {
      const value = data.indicators.strategy?.[name]?.values[hoveredIndex]
      const colorIndex = selectedIndicators.indexOf(name)
      return [{ name, value, color: STRATEGY_INDICATOR_COLORS[colorIndex % STRATEGY_INDICATOR_COLORS.length] }]
    })
  const formatValue = (value: number | null | undefined) => (
    value === null || value === undefined || !Number.isFinite(value)
      ? '—'
      : new Intl.NumberFormat(undefined, { maximumSignificantDigits: 7 }).format(value)
  )
  const toggle = (key: keyof typeof visible) => setVisible((current) => ({ ...current, [key]: !current[key] }))
  const zoom = (direction: -1 | 1) => {
    setHoveredIndex(null)
    const nextZoomLevel = Math.max(0, Math.min(MAX_ZOOM_LEVEL, zoomLevel + direction))
    const nextVisibleCount = Math.max(1, Math.ceil(data.candles.length / (1.5 ** nextZoomLevel)))
    setZoomLevel(nextZoomLevel)
    setPanOffset((current) => Math.min(current, Math.max(0, data.candles.length - nextVisibleCount)))
  }
  const resetZoom = () => {
    setZoomLevel(0)
    setPanOffset(0)
    setHoveredIndex(null)
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
  const updateHoveredCandle = (event: React.PointerEvent<SVGSVGElement>) => {
    const chartX = pointerPosition(event.currentTarget, event.clientX, event.clientY).x
    const index = visibleStartIndex + Math.round(((chartX - padding) / plotWidth) * Math.max(visibleEndIndex - visibleStartIndex, 1))
    setHoveredIndex(Math.max(visibleStartIndex, Math.min(visibleEndIndex, index)))
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
          <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => zoom(1)} disabled={zoomLevel === MAX_ZOOM_LEVEL || data.candles.length <= 1}>+</button>
          <button type="button" className="chart-zoom-reset" onClick={resetZoom} disabled={zoomLevel === 0 && visibleStartIndex === 0}>Reset zoom</button>
        </div>
      </div>
      <div className="forex-chart-stage">
        <svg className={`forex-chart${isPanning ? ' is-panning' : ''}${large ? ' is-large' : ''}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.pair} price chart with percentage scale. Drag horizontally to pan through candles.`} onPointerDown={(event) => {
          if (event.button !== 0 || isReferenceDragging.current) return
          suppressClick.current = false
          const position = pointerPosition(event.currentTarget, event.clientX, event.clientY)
          panStart.current = { pointerId: event.pointerId, x: position.x, offset: Math.min(panOffset, maxPanOffset), moved: false }
          event.currentTarget.setPointerCapture(event.pointerId)
        }} onPointerMove={(event) => {
          if (isReferenceDragging.current) {
            moveReferenceToPointer(event)
            return
          }
          const chartX = pointerPosition(event.currentTarget, event.clientX, event.clientY).x
          const currentPan = panStart.current
          if (currentPan?.pointerId === event.pointerId) {
            const delta = chartX - currentPan.x
            if (Math.abs(delta) >= 3) {
              currentPan.moved = true
              setIsPanning(true)
              setHoveredIndex(null)
            }
            if (currentPan.moved) {
              const offset = currentPan.offset + Math.round((delta / plotWidth) * visibleCount)
              setPanOffset(Math.max(0, Math.min(maxPanOffset, offset)))
              return
            }
          }
          const index = visibleStartIndex + Math.round(((chartX - padding) / plotWidth) * Math.max(visibleEndIndex - visibleStartIndex, 1))
          setHoveredIndex(Math.max(visibleStartIndex, Math.min(visibleEndIndex, index)))
        }} onPointerUp={(event) => {
          if (panStart.current?.pointerId !== event.pointerId) return
          suppressClick.current = panStart.current.moved
          panStart.current = null
          setIsPanning(false)
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
        }} onPointerCancel={(event) => {
          if (panStart.current?.pointerId !== event.pointerId) return
          panStart.current = null
          setIsPanning(false)
        }} onClick={() => {
          if (suppressClick.current) {
            suppressClick.current = false
            return
          }
          if (!isReferenceDragging.current && hoveredCandle) onPriceSelect?.(hoveredCandle.close)
        }} onPointerLeave={() => { if (!isReferenceDragging.current && !panStart.current) setHoveredIndex(null) }}>
          {visible.candles && data.candles.slice(visibleStartIndex, visibleEndIndex + 1).map((candle, offset) => {
            const index = visibleStartIndex + offset
            const x = chart.scaleX(index)
            const openY = chart.scaleY(candle.open)
            const closeY = chart.scaleY(candle.close)
            const highY = chart.scaleY(candle.high)
            const lowY = chart.scaleY(candle.low)
            const bullish = candle.close >= candle.open
            const color = bullish ? '#34d399' : '#fb7185'
            const bodyHeight = Math.max(2.5, Math.abs(closeY - openY))
            const bodyCenter = (openY + closeY) / 2
            return <g key={candle.time} className="chart-candle"><line x1={x} x2={x} y1={highY} y2={lowY} stroke={color} strokeWidth="1.2" /><rect x={x - chart.candleWidth / 2} y={bodyCenter - bodyHeight / 2} width={chart.candleWidth} height={bodyHeight} fill={color} stroke={color} strokeWidth="0.75" /></g>
          })}
          {visible.close && <polyline points={chart.line(data.candles.slice(visibleStartIndex, visibleEndIndex + 1).map((candle) => candle.close), visibleStartIndex)} fill="none" stroke="#38bdf8" strokeWidth="1.5" />}
          {visible.ema20 && <polyline points={chart.line(chart.ema20.slice(visibleStartIndex, visibleEndIndex + 1), visibleStartIndex)} fill="none" stroke="#fbbf24" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ema50 && <polyline points={chart.line(chart.ema50.slice(visibleStartIndex, visibleEndIndex + 1), visibleStartIndex)} fill="none" stroke="#c084fc" strokeWidth="1.5" strokeDasharray="5 4" />}
          {visible.ema100 && <polyline points={chart.line(chart.ema100.slice(visibleStartIndex, visibleEndIndex + 1), visibleStartIndex)} fill="none" stroke="#f97316" strokeWidth="1.5" strokeDasharray="5 4" />}
          {chart.overlaySeries.map((indicator, index) => {
            const selectedIndex = selectedIndicators.indexOf(indicator.name)
            return <path key={indicator.name} d={indicatorPath(indicator.values.slice(visibleStartIndex, visibleEndIndex + 1), visibleStartIndex, chart.scaleX, chart.scaleY)} fill="none" stroke={STRATEGY_INDICATOR_COLORS[(selectedIndex < 0 ? index : selectedIndex) % STRATEGY_INDICATOR_COLORS.length]} strokeWidth="1.8"><title>{indicator.name}</title></path>
          })}
          {chart.signalMarkers.map((signal, index) => {
            const signalType = signal.signalType ?? (signal.side === 'BUY' ? 'entry_long' : 'entry_short')
            const isExit = signalType === 'exit_long' || signalType === 'exit_short'
            const isLong = signalType === 'entry_long' || signalType === 'exit_long'
            if (isExit ? !visible[signalType === 'exit_long' ? 'exitLong' : 'exitShort'] : !visible.signals && !visible[signal.side === 'BUY' ? 'buy' : 'sell']) return null
            const label = isExit ? `EXIT ${isLong ? 'LONG' : 'SHORT'}` : `${isLong ? 'BUY' : 'SELL'} ENTRY`
            const labelWidth = isExit ? 68 : 72
            const labelX = signal.x + labelWidth + 11 > plotRight - 4
              ? signal.x - labelWidth - 11
              : signal.x + 11
            return (
              <g key={`${signal.time}-${signalType}-${index}`} className={`chart-signal-marker ${isExit ? 'is-exit' : 'is-entry'} ${isLong ? 'is-long' : 'is-short'}`}>
                {isExit ? (
                  <polygon points={isLong
                    ? `${signal.x - 8},${signal.y - 7} ${signal.x + 8},${signal.y - 7} ${signal.x},${signal.y + 8}`
                    : `${signal.x},${signal.y - 8} ${signal.x + 8},${signal.y + 7} ${signal.x - 8},${signal.y + 7}`}
                  />
                ) : (
                  <circle cx={signal.x} cy={signal.y} r="6" />
                )}
                <rect x={labelX} y={signal.y - 10} width={labelWidth} height="18" rx="5" />
                <text x={labelX + 5} y={signal.y + 3}>{label}</text>
                <title>{label} · approved strategy {signal.sourceTimeframe}</title>
              </g>
            )
          })}
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
        {hoveredIndex !== null && hoveredCandle && (
          <div className="chart-tooltip">
            <strong>{new Date(hoveredCandle.time).toLocaleString()}</strong>
            <span>O {formatValue(hoveredCandle.open)} · H {formatValue(hoveredCandle.high)}</span>
            <span>L {formatValue(hoveredCandle.low)} · C {formatValue(hoveredCandle.close)}</span>
            {visible.ema20 && <span><i className="tooltip-color" style={{ background: '#fbbf24' }} />EMA 20 {formatValue(chart.ema20[hoveredIndex])}</span>}
            {visible.ema50 && <span><i className="tooltip-color" style={{ background: '#c084fc' }} />EMA 50 {formatValue(chart.ema50[hoveredIndex])}</span>}
            {visible.ema100 && <span><i className="tooltip-color" style={{ background: '#f97316' }} />EMA 100 {formatValue(chart.ema100[hoveredIndex])}</span>}
            {hoveredIndicatorValues.map((indicator) => (
              <span key={indicator.name}><i className="tooltip-color" style={{ background: indicator.color }} />{indicator.name} {formatValue(indicator.value)}</span>
            ))}
            {hoveredSignal && <span className={hoveredSignal.side === 'BUY' ? 'tooltip-buy' : 'tooltip-sell'}>{hoveredSignal.signalType?.replace('_', ' ').toUpperCase() ?? `Strategy ${hoveredSignal.side}`} · {hoveredSignal.sourceTimeframe}</span>}
          </div>
        )}
      </div>
      {chart.lowerPanels.map((panel) => {
        const panelHeight = panelHeights[panel.name] ?? 120
        const isCollapsed = Boolean(collapsedPanels[panel.name])
        const hoveredValue = hoveredIndex === null
          ? data.indicators.strategy?.[panel.name]?.values[visibleEndIndex]
          : data.indicators.strategy?.[panel.name]?.values[hoveredIndex]
        return (
          <section className={`strategy-indicator-panel${isCollapsed ? ' is-collapsed' : ''}`} key={panel.name}>
            <div className="strategy-indicator-heading">
              <strong style={{ color: panel.color }}>{panel.name}</strong>
              <span className="indicator-panel-current" style={{ color: panel.color }}>{formatValue(hoveredValue)}</span>
              <button
                type="button"
                aria-expanded={!isCollapsed}
                aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${panel.name} panel`}
                onClick={() => setCollapsedPanels((current) => ({ ...current, [panel.name]: !current[panel.name] }))}
              >
                {isCollapsed ? 'Expand' : 'Collapse'}
              </button>
            </div>
            {!isCollapsed && (
              <>
                <svg
                  className="strategy-indicator-chart"
                  style={{ height: `${panelHeight}px` }}
                  viewBox={`0 0 ${width} 120`}
                  role="img"
                  aria-label={`${panel.name} indicator panel`}
                  onPointerMove={updateHoveredCandle}
                  onPointerLeave={() => setHoveredIndex(null)}
                >
                  {panel.isRsi ? (
                    <>
                      <line x1={padding} x2={plotRight} y1={panel.scalePanelY(70)} y2={panel.scalePanelY(70)} className="indicator-guide-line" />
                      <line x1={padding} x2={plotRight} y1={panel.scalePanelY(30)} y2={panel.scalePanelY(30)} className="indicator-guide-line" />
                      <text x={plotRight + 10} y={panel.scalePanelY(70) - 3} className="indicator-panel-value">70</text>
                      <text x={plotRight + 10} y={panel.scalePanelY(30) - 3} className="indicator-panel-value">30</text>
                    </>
                  ) : (
                    <line x1={padding} x2={plotRight} y1="64" y2="64" className="chart-zero-line" />
                  )}
                  <path d={panel.path} fill="none" stroke={panel.color} strokeWidth="1.8" />
                  {hoveredIndex !== null && <line x1={chart.scaleX(hoveredIndex)} x2={chart.scaleX(hoveredIndex)} y1="12" y2="108" className="indicator-crosshair" />}
                </svg>
                <div
                  className="indicator-panel-resize"
                  role="separator"
                  aria-label={`Resize ${panel.name} panel`}
                  aria-orientation="horizontal"
                  aria-valuemin={80}
                  aria-valuemax={260}
                  aria-valuenow={panelHeight}
                  tabIndex={0}
                  onPointerDown={(event) => {
                    event.preventDefault()
                    panelResizeStart.current = { name: panel.name, pointerId: event.pointerId, y: event.clientY, height: panelHeight }
                    event.currentTarget.setPointerCapture(event.pointerId)
                  }}
                  onPointerMove={(event) => {
                    const start = panelResizeStart.current
                    if (!start || start.pointerId !== event.pointerId) return
                    const nextHeight = Math.max(80, Math.min(260, start.height + event.clientY - start.y))
                    setPanelHeights((current) => ({ ...current, [start.name]: nextHeight }))
                  }}
                  onPointerUp={(event) => {
                    if (panelResizeStart.current?.pointerId !== event.pointerId) return
                    panelResizeStart.current = null
                    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
                  }}
                  onPointerCancel={() => { panelResizeStart.current = null }}
                  onKeyDown={(event) => {
                    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
                    event.preventDefault()
                    const change = event.key === 'ArrowUp' ? 10 : -10
                    setPanelHeights((current) => ({ ...current, [panel.name]: Math.max(80, Math.min(260, panelHeight + change)) }))
                  }}
                />
              </>
            )}
          </section>
        )
      })}
      <div className="chart-legend" aria-label="Chart layers">
        <button type="button" aria-pressed={visible.close} className={`legend-toggle${visible.close ? ' active' : ''}`} onClick={() => toggle('close')}><i className="legend-line price" />Close</button>
        <button type="button" aria-pressed={visible.ema20} className={`legend-toggle${visible.ema20 ? ' active' : ''}`} onClick={() => toggle('ema20')}><i className="legend-line ema" />EMA 20</button>
        <button type="button" aria-pressed={visible.ema50} className={`legend-toggle${visible.ema50 ? ' active' : ''}`} onClick={() => toggle('ema50')}><i className="legend-line ema50" />EMA 50</button>
        <button type="button" aria-pressed={visible.ema100} className={`legend-toggle${visible.ema100 ? ' active' : ''}`} onClick={() => toggle('ema100')}><i className="legend-line ema100" />EMA 100</button>
        <button type="button" aria-pressed={visible.trades} className={`legend-toggle${visible.trades ? ' active' : ''}`} onClick={() => toggle('trades')}><i className="legend-dot trade" />Trades</button>
        <button type="button" aria-pressed={visible.signals} className={`legend-toggle${visible.signals ? ' active' : ''}`} onClick={() => toggle('signals')}><i className="legend-dot buy" />Signals</button>
        <button type="button" aria-pressed={visible.buy} className={`legend-toggle${visible.buy ? ' active' : ''}`} onClick={() => toggle('buy')}><i className="legend-dot buy" />Buy</button>
        <button type="button" aria-pressed={visible.sell} className={`legend-toggle${visible.sell ? ' active' : ''}`} onClick={() => toggle('sell')}><i className="legend-dot sell" />Sell</button>
        <button type="button" aria-pressed={visible.exitLong} className={`legend-toggle${visible.exitLong ? ' active' : ''}`} onClick={() => toggle('exitLong')}><i className="legend-exit-long" />Exit long</button>
        <button type="button" aria-pressed={visible.exitShort} className={`legend-toggle${visible.exitShort ? ' active' : ''}`} onClick={() => toggle('exitShort')}><i className="legend-exit-short" />Exit short</button>
      </div>
    </div>
  )
}
