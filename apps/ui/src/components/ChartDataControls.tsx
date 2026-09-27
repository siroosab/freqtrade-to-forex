import { CHART_CANDLE_OPTIONS, OANDA_CHART_TIMEFRAMES } from './chartOptions'

type ChartDataControlsProps = {
  timeframe: string
  countMultiplier: number
  onTimeframeChange: (timeframe: string) => void
  onCountMultiplierChange: (multiplier: number) => void
}

export function ChartDataControls({ timeframe, countMultiplier, onTimeframeChange, onCountMultiplierChange }: ChartDataControlsProps) {
  return (
    <div className="chart-controls">
      <select aria-label="Chart timeframe" value={timeframe} onChange={(event) => onTimeframeChange(event.target.value)}>
        {OANDA_CHART_TIMEFRAMES.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
      <select aria-label="Candles to load" value={countMultiplier} onChange={(event) => onCountMultiplierChange(Number(event.target.value))}>
        {CHART_CANDLE_OPTIONS.map(({ multiplier, count }) => <option key={multiplier} value={multiplier}>{multiplier}x ({count})</option>)}
      </select>
    </div>
  )
}