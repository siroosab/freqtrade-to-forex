import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getOrdersChart, getSetupStatus } from '../api/mockApi'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount, chartTimeframeFromStrategy } from '../components/chartOptions'
import { ForexChart } from '../components/ForexChart'
import { useUiStore } from '../store/useUiStore'

function defaultIndicators(indicators: NonNullable<Awaited<ReturnType<typeof getOrdersChart>>['indicators']['strategy']>) {
  const entries = Object.entries(indicators)
  const overlays = entries.filter(([, indicator]) => indicator.panel === 'overlay')
  const lower = entries.filter(([, indicator]) => indicator.panel === 'lower')
  const preferredOverlays = overlays.filter(([name]) => /basis|upper|lower|ema|sma|ma/i.test(name))
  return [
    ...(preferredOverlays.length ? preferredOverlays : overlays).slice(0, 3),
    ...lower.slice(0, 2),
  ].map(([name]) => name)
}

function formatIndicatorValue(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value)
    ? '—'
    : new Intl.NumberFormat(undefined, { maximumSignificantDigits: 7 }).format(value)
}

export function IndicatorsPage() {
  const availablePairs = useUiStore((state) => state.selectedInstruments)
  const [selectedPair, setSelectedPair] = useState(availablePairs[0] ?? 'EUR/USD')
  const pair = availablePairs.length && !availablePairs.includes(selectedPair)
    ? availablePairs[0]
    : selectedPair
  const setupQuery = useQuery({ queryKey: ['setup-status'], queryFn: getSetupStatus })
  const [timeframeOverride, setTimeframeOverride] = useState<string | null>(null)
  const pairKey = pair.replace(/\//g, '_').toUpperCase()
  const timeframe = timeframeOverride
    ?? chartTimeframeFromStrategy(setupQuery.data?.pairTimeframes[pairKey], 'M15')
  const [countMultiplier, setCountMultiplier] = useState(1)
  const chartQuery = useQuery({
    queryKey: ['indicator-chart', pair, timeframe, countMultiplier],
    queryFn: () => getOrdersChart(pair, timeframe, chartCandleCount(countMultiplier)),
    refetchInterval: 30_000,
    enabled: !setupQuery.isLoading,
    retry: false,
  })
  const [selectedIndicators, setSelectedIndicators] = useState<string[]>([])
  const initializedScope = useRef('')
  const indicatorData = chartQuery.data?.indicators.strategy ?? {}

  useEffect(() => {
    if (!chartQuery.data) return
    const scope = `${chartQuery.data.pair}:${chartQuery.data.approvedStrategy}:${chartQuery.data.approvedTimeframe}`
    if (initializedScope.current === scope) return
    initializedScope.current = scope
    setSelectedIndicators(defaultIndicators(chartQuery.data.indicators.strategy ?? {}))
  }, [chartQuery.data])

  const toggleIndicator = (name: string) => {
    setSelectedIndicators((current) => current.includes(name)
      ? current.filter((item) => item !== name)
      : [...current, name])
  }
  const overlayIndicators = Object.entries(indicatorData).filter(([, indicator]) => indicator.panel === 'overlay')
  const lowerIndicators = Object.entries(indicatorData).filter(([, indicator]) => indicator.panel === 'lower')

  return (
    <div className="indicators-page">
      <header className="topbar">
        <div>
          <p className="eyebrow">Technical analysis</p>
          <h2>Indicators</h2>
          <p className="orders-source-note">Indicators are calculated directly from the approved strategy for each pair.</p>
        </div>
      </header>

      <section className="panel page-panel indicators-chart-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Market context</p>
            <h3>Price chart and indicators</h3>
            {chartQuery.data && (
              <p className="indicator-strategy-context">
                {chartQuery.data.pair} · {chartQuery.data.approvedStrategy} · Strategy timeframe {chartQuery.data.approvedTimeframe}
              </p>
            )}
          </div>
          <div className="chart-controls">
            <select aria-label="Chart pair" value={pair} onChange={(event) => setSelectedPair(event.target.value)}>
              {(availablePairs.length ? availablePairs : ['EUR/USD', 'GBP/USD', 'USD/JPY']).map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <ChartDataControls
              timeframe={timeframe}
              countMultiplier={countMultiplier}
              onTimeframeChange={setTimeframeOverride}
              onCountMultiplierChange={setCountMultiplier}
              extendedCandleOptions
            />
          </div>
        </div>

        {chartQuery.isLoading && <p>Loading candles and calculating strategy indicators…</p>}
        {chartQuery.isError && (
          <p role="alert">{chartQuery.error instanceof Error ? chartQuery.error.message : 'Chart and indicator data is unavailable.'}</p>
        )}
        {chartQuery.data && Object.keys(indicatorData).length === 0 && (
          <p className="indicator-empty-note">This strategy did not return any numeric indicators to display.</p>
        )}
        {chartQuery.data && Object.keys(indicatorData).length > 0 && (
          <details className="indicator-picker">
            <summary className="indicator-picker-summary">
              <span>
                <strong>Strategy indicators</strong>
                <span>Select indicators to add or remove them from the chart.</span>
              </span>
              <span className="indicator-picker-count">{selectedIndicators.length} selected</span>
            </summary>
            <div className="indicator-picker-content">
              <div className="indicator-picker-actions">
                <span>Choose overlays and lower panels to customize this strategy view.</span>
                <button type="button" className="secondary-button" onClick={() => setSelectedIndicators([])} disabled={!selectedIndicators.length}>
                  Clear selection
                </button>
              </div>
              <div className="indicator-picker-groups">
                {[
                  { title: 'Price overlays', items: overlayIndicators },
                  { title: 'Lower panels', items: lowerIndicators },
                ].map((group) => (
                  <fieldset className="indicator-picker-group" key={group.title}>
                    <legend>{group.title}</legend>
                    {group.items.length === 0 && <span className="indicator-empty-note">None available</span>}
                    {group.items.map(([name, indicator]) => {
                      const latest = indicator.values.at(-1)
                      const selected = selectedIndicators.includes(name)
                      return (
                        <button
                          aria-pressed={selected}
                          className={`indicator-chip${selected ? ' selected' : ''}`}
                          key={name}
                          onClick={() => toggleIndicator(name)}
                          title={`Latest value: ${formatIndicatorValue(latest)}`}
                          type="button"
                        >
                          <span>{name}</span>
                          <strong>{formatIndicatorValue(latest)}</strong>
                        </button>
                      )
                    })}
                  </fieldset>
                ))}
              </div>
            </div>
          </details>
        )}
        {chartQuery.data && <ForexChart data={chartQuery.data} selectedIndicators={selectedIndicators} large />}
      </section>

      <section className="panel indicator-analysis-panel">
        <div>
          <p className="eyebrow">Strategy analysis</p>
          <h3>Selected indicator summary</h3>
        </div>
        {!selectedIndicators.length && <p className="indicator-empty-note">Select one or more indicators above to view their summary.</p>}
        {!!selectedIndicators.length && (
          <div className="indicator-summary-grid">
            {selectedIndicators.map((name) => {
              const indicator = indicatorData[name]
              if (!indicator) return null
              const values = indicator.values.filter((value): value is number => value !== null && Number.isFinite(value))
              const latest = values.at(-1)
              const previous = values.at(-2)
              const change = latest !== undefined && previous !== undefined ? latest - previous : null
              return (
                <article className="indicator-summary-card" key={name}>
                  <span>{name}</span>
                  <strong>{formatIndicatorValue(latest)}</strong>
                  <small>{change === null ? 'Change: —' : `Last candle change: ${change > 0 ? '+' : ''}${formatIndicatorValue(change)}`}</small>
                </article>
              )
            })}
          </div>
        )}
        {chartQuery.data && (
          <p className="indicator-analysis-note">
            Values use the approved {chartQuery.data.approvedStrategy} strategy revision. Indicator panels stay synchronized with chart navigation and zoom.
          </p>
        )}
      </section>
    </div>
  )
}
