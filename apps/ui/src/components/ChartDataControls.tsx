import { CHART_CANDLE_OPTIONS, OANDA_CHART_TIMEFRAMES } from './chartOptions'

type ChartDataControlsProps = {
  timeframe: string
  countMultiplier: number
  onTimeframeChange: (timeframe: string) => void
  onCountMultiplierChange: (multiplier: number) => void
  extendedCandleOptions?: boolean
}

export function ChartDataControls({ timeframe, countMultiplier, onTimeframeChange, onCountMultiplierChange, extendedCandleOptions = false }: ChartDataControlsProps) {
  const candleOptions = extendedCandleOptions
    ? CHART_CANDLE_OPTIONS
    : CHART_CANDLE_OPTIONS.filter(({ multiplier }) => multiplier <= 4)

  return (
    <div className="chart-controls">
      <select aria-label="Chart timeframe" value={timeframe} onChange={(event) => onTimeframeChange(event.target.value)}>
        {OANDA_CHART_TIMEFRAMES.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
      <select aria-label="Candles to load" value={countMultiplier} onChange={(event) => onCountMultiplierChange(Number(event.target.value))}>
        {candleOptions.map(({ multiplier, count }) => <option key={multiplier} value={multiplier}>{multiplier}x ({count})</option>)}
      </select>
    </div>
  )
}