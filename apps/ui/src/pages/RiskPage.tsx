import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ForexChart } from '../components/ForexChart'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount } from '../components/chartOptions'
import {
  applyBrokerRiskProtection,
  createAverageEntryOrder,
  getBrokerPositions,
  getMarketQuote,
  getOrdersChart,
  getRiskConfig,
  getRiskSummary,
  saveRiskConfig,
  type LiveQuote,
  type RiskConfig,
} from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'
import { convertTrailingStopPipsToDistance } from '../utils/trailingStopLoss'

type ProtectionMode = 'price' | 'percent' | 'pips'
type ProtectedValue = 'stopLoss' | 'takeProfit' | 'averageEntry'

function resolvePrice(
  value: string | null,
  mode: ProtectionMode,
  reference: number,
  pipSize: number,
  precision: number,
  side: 'BUY' | 'SELL',
  kind: ProtectedValue,
): string | null {
  if (!value?.trim()) return null
  const amount = Number(value)
  if (!Number.isFinite(amount) || amount <= 0) return null
  if (mode === 'price') return amount.toFixed(precision)
  if (!Number.isFinite(reference) || reference <= 0) return null
  const distance = mode === 'pips' ? amount * pipSize : reference * amount / 100
  if (!Number.isFinite(distance) || distance <= 0) return null
  const movesHigher = kind === 'averageEntry'
    ? side === 'SELL'
    : (kind === 'takeProfit') === (side === 'BUY')
  const price = reference + (movesHigher ? distance : -distance)
  return price > 0 ? price.toFixed(precision) : null
}

function displayedValues(priceText: string | null, reference: number, pipSize: number, precision: number) {
  const price = Number(priceText)
  if (!priceText || !Number.isFinite(price) || !Number.isFinite(reference) || reference <= 0) {
    return { price: priceText ?? '—', percent: '—', pips: '—' }
  }
  const difference = Math.abs(price - reference)
  return {
    price: price.toFixed(precision),
    percent: `${(difference / reference * 100).toFixed(3)}%`,
    pips: Number.isFinite(pipSize) && pipSize > 0 ? (difference / pipSize).toFixed(1) : '—',
  }
}

export function RiskPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery({ queryKey: ['risk'], queryFn: getRiskSummary })
  const availablePairs = useUiStore((state) => state.selectedInstruments)
  const [pair, setPair] = useState(availablePairs[0] ?? 'EUR/USD')
  const selectedPair = availablePairs.length && !availablePairs.includes(pair) ? availablePairs[0] : pair
  const [timeframe, setTimeframe] = useState('M15')
  const [countMultiplier, setCountMultiplier] = useState(1)
  const configQuery = useQuery({ queryKey: ['risk-config', selectedPair], queryFn: () => getRiskConfig(selectedPair) })
  const chartQuery = useQuery({
    queryKey: ['risk-chart', selectedPair, timeframe, countMultiplier],
    queryFn: () => getOrdersChart(selectedPair, timeframe, chartCandleCount(countMultiplier)),
    refetchInterval: 30000,
  })
  const volatilityQuery = useQuery({
    queryKey: ['risk-volatility', selectedPair],
    queryFn: () => getOrdersChart(selectedPair, 'D', 30),
    refetchInterval: 300000,
  })
  const quoteQuery = useQuery({
    queryKey: ['risk-live-quote', selectedPair],
    queryFn: () => getMarketQuote(selectedPair),
    refetchInterval: 3000,
    retry: false,
  })
  const positionsQuery = useQuery({
    queryKey: ['broker-positions'],
    queryFn: getBrokerPositions,
    refetchInterval: 3000,
    retry: false,
  })
  const [formDrafts, setFormDrafts] = useState<Record<string, RiskConfig>>({})
  const saveMutation = useMutation({
    mutationFn: saveRiskConfig,
    onSuccess: async (savedConfig) => {
      setFormDrafts((current) => ({ ...current, [savedConfig.pair]: savedConfig }))
      queryClient.setQueryData(['risk-config', savedConfig.pair], savedConfig)
      await queryClient.invalidateQueries({
        queryKey: ['risk-config', savedConfig.pair],
      })
    },
  })
  const protectionMutation = useMutation({
    mutationFn: ({ tradeId, payload }: {
      tradeId: string
      payload: { stopLoss: string | null; takeProfit: string | null; trailingStopLossDistance: string | null }
    }) => applyBrokerRiskProtection(tradeId, payload),
    onSuccess: async () => {
      setExecutionFeedback({ message: 'Protection update sent to OANDA.', error: false })
      await queryClient.invalidateQueries({ queryKey: ['broker-positions'] })
    },
    onError: (error) => setExecutionFeedback({
      message: error instanceof Error ? error.message : 'OANDA protection update failed.',
      error: true,
    }),
  })
  const averageMutation = useMutation({
    mutationFn: ({ tradeId, payload }: {
      tradeId: string
      payload: { units: string; price: string; stopLoss: string | null; takeProfit: string | null }
    }) => createAverageEntryOrder(tradeId, payload),
    onSuccess: async () => {
      setExecutionFeedback({
        message: 'Average-entry limit order placed at OANDA; it will be cancelled if its parent trade closes.',
        error: false,
      })
      await queryClient.invalidateQueries({ queryKey: ['broker-pending-orders'] })
    },
    onError: (error) => setExecutionFeedback({
      message: error instanceof Error ? error.message : 'OANDA average-entry order failed.',
      error: true,
    }),
  })
  const [selection, setSelection] = useState<ProtectedValue>('stopLoss')
  const [selectedTradeId, setSelectedTradeId] = useState('')
  const [averageUnitsByTrade, setAverageUnitsByTrade] = useState<Record<string, string>>({})
  const [trailingStopPips, setTrailingStopPips] = useState('')
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null)
  const [executionFeedback, setExecutionFeedback] = useState<{ message: string; error: boolean } | null>(null)

  const pairConfig = configQuery.data?.pair.replace('_', '/').toUpperCase() === selectedPair.toUpperCase()
    ? configQuery.data
    : null
  const form = formDrafts[selectedPair] ?? pairConfig
  const update = <Key extends keyof RiskConfig,>(key: Key, value: RiskConfig[Key]) => setFormDrafts((current) => {
    const config = current[selectedPair] ?? configQuery.data
    return config ? { ...current, [selectedPair]: { ...config, [key]: value } } : current
  })
  const trades = useMemo(
    () => (positionsQuery.data?.open ?? []).filter((trade) => trade.symbol === selectedPair),
    [selectedPair, positionsQuery.data?.open],
  )
  const selectedTrade = trades.find((trade) => trade.id === selectedTradeId) ?? trades[0]
  const averageUnits = selectedTrade
    ? averageUnitsByTrade[selectedTrade.id] ?? selectedTrade.units
    : ''

  const quote: LiveQuote | undefined = quoteQuery.data
  const availableUnits = quote?.unitsAvailable?.default
  const precision = quote?.displayPrecision ?? (selectedPair.endsWith('/JPY') ? 3 : 5)
  const pipSize = Number(quote?.pipSize)
  const side = selectedTrade?.side ?? 'BUY'
  const entryReference = Number(selectedTrade?.entryPrice)
  const marketReference = selectedTrade?.currentPrice
    ? Number(selectedTrade.currentPrice)
    : quote ? (Number(quote.bid) + Number(quote.ask)) / 2 : Number.NaN
  const protectionReference = Number.isFinite(entryReference) && entryReference > 0
    ? entryReference
    : marketReference
  const averageReference = Number.isFinite(marketReference) && marketReference > 0
    ? marketReference
    : protectionReference
  const stopPrice = resolvePrice(form?.stopLoss ?? null, form?.stopLossMode ?? 'price', protectionReference, pipSize, precision, side, 'stopLoss')
  const takeProfitPrice = resolvePrice(form?.takeProfit ?? null, form?.takeProfitMode ?? 'price', protectionReference, pipSize, precision, side, 'takeProfit')
  const averagePrice = resolvePrice(form?.averageEntry ?? null, form?.averageEntryMode ?? 'price', averageReference, pipSize, precision, side, 'averageEntry')
  const stopLossConfigured = Boolean(form?.stopLoss?.trim())
  const takeProfitConfigured = Boolean(form?.takeProfit?.trim())
  const averageEntryConfigured = Boolean(form?.averageEntry?.trim())
  const stopLossInvalid = stopLossConfigured && !stopPrice
  const takeProfitInvalid = takeProfitConfigured && !takeProfitPrice
  const averageEntryInvalid = averageEntryConfigured && !averagePrice
  const stopValues = displayedValues(stopPrice, protectionReference, pipSize, precision)
  const takeProfitValues = displayedValues(takeProfitPrice, protectionReference, pipSize, precision)
  const averageValues = displayedValues(averagePrice, averageReference, pipSize, precision)
  const trailingDistance = convertTrailingStopPipsToDistance(trailingStopPips, pipSize, precision)
  const volatilityCandles = volatilityQuery.data?.candles.slice(-7) ?? []
  const volatilityHigh = volatilityCandles.length ? Math.max(...volatilityCandles.map((candle) => candle.high)) : Number.NaN
  const volatilityLow = volatilityCandles.length ? Math.min(...volatilityCandles.map((candle) => candle.low)) : Number.NaN
  const latestPrice = quote ? (Number(quote.bid) + Number(quote.ask)) / 2 : Number.NaN
  const volatilityRange = volatilityHigh - volatilityLow
  const pricePositionPercent = Number.isFinite(latestPrice)
    && Number.isFinite(volatilityLow)
    && Number.isFinite(volatilityHigh)
    && volatilityRange > 0
    ? (latestPrice - volatilityLow) / volatilityRange * 100
    : Number.NaN
  const rangeMarkerPosition = Number.isFinite(pricePositionPercent)
    ? Math.min(100, Math.max(0, pricePositionPercent))
    : 0
  const rangeLabelPosition = Math.min(92, Math.max(8, rangeMarkerPosition))
  const priceRangeStatus = Number.isFinite(pricePositionPercent)
    ? pricePositionPercent < 0
      ? 'Below 7-day low'
      : pricePositionPercent > 100
        ? 'Above 7-day high'
        : 'Inside 7-day range'
    : 'Price/range unavailable'
  const volatilityPercent = Number.isFinite(volatilityHigh) && Number.isFinite(volatilityLow) && volatilityLow > 0
    ? (volatilityHigh - volatilityLow) / volatilityLow * 100
    : Number.NaN
  const pairProfitLoss = trades.reduce((total, trade) => total + Number(trade.pnl || 0), 0)
  const accountCurrency = positionsQuery.data?.accountCurrency ?? quote?.accountCurrency ?? ''

  const setChartPrice = (price: number) => {
    if (!form) return
    const modeKey: keyof RiskConfig = selection === 'stopLoss'
      ? 'stopLossMode'
      : selection === 'takeProfit' ? 'takeProfitMode' : 'averageEntryMode'
    setFormDrafts((current) => ({
      ...current,
      [selectedPair]: {
        ...form,
        [selection]: price.toFixed(precision),
        [modeKey]: 'price',
      },
    }))
  }

  const modeOptions = (value: ProtectionMode, onChange: (mode: ProtectionMode) => void) => (
    <select value={value} onChange={(event) => onChange(event.target.value as ProtectionMode)}>
      <option value="price">Price</option><option value="percent">%</option><option value="pips">Pips</option>
    </select>
  )
  const renderValues = (values: ReturnType<typeof displayedValues>) => (
    <small className="calculated-price">Price: {values.price} · %: {values.percent} · Pips: {values.pips}</small>
  )

  return (
    <>
      <header className="topbar">
        <div><p className="eyebrow">CONTROL CENTER / RISK</p><h2>Controls and exposure</h2><p className="settings-intro">Every order passes through these limits before it reaches the broker.</p></div>
        <span className={`status-pill ${data?.killSwitch ? 'negative' : 'online'}`}>{data?.killSwitch ? 'Kill switch active' : 'Risk controls clear'}</span>
      </header>

      {data && <section className="risk-snapshot" aria-label="Risk snapshot">
        <div><span>Daily loss</span><strong>{data.dailyLoss}</strong><small>Policy limit</small></div>
        <div><span>Max exposure</span><strong>{data.maxExposure}</strong><small>Across open positions</small></div>
        <div><span>Margin level</span><strong>{data.marginLevel}</strong><small>Account health</small></div>
        <div className={data.killSwitch ? 'danger' : 'safe'}><span>Execution gate</span><strong>{data.killSwitch ? 'Blocked' : 'Armed'}</strong><small>{data.killSwitch ? 'Manual review required' : 'Pre-trade checks active'}</small></div>
      </section>}

      <section className="panel page-panel">
        <div className="panel-header compact"><div><p className="eyebrow">Risk map</p><h3>Price-based protection</h3></div><div className="chart-controls"><select aria-label="Risk pair" value={selectedPair} onChange={(event) => setPair(event.target.value)}>{(availablePairs.length ? availablePairs : ['EUR/USD', 'GBP/USD', 'USD/JPY']).map((item) => <option key={item} value={item}>{item}</option>)}</select><ChartDataControls timeframe={timeframe} countMultiplier={countMultiplier} onTimeframeChange={setTimeframe} onCountMultiplierChange={setCountMultiplier} /></div></div>
        <p className="risk-helper">Select a protection level, then click the chart. Prices and trade status below are refreshed from OANDA.</p>
        <div className="chart-controls risk-click-controls"><button type="button" className={selection === 'stopLoss' ? 'selected' : ''} onClick={() => setSelection('stopLoss')}>Set stop loss</button><button type="button" className={selection === 'takeProfit' ? 'selected' : ''} onClick={() => setSelection('takeProfit')}>Set take profit</button><button type="button" className={selection === 'averageEntry' ? 'selected' : ''} onClick={() => setSelection('averageEntry')}>Set average entry</button></div>
        {chartQuery.isLoading && <p>Loading OANDA chart…</p>}
        {chartQuery.isError && <p role="alert">{chartQuery.error instanceof Error ? chartQuery.error.message : 'Risk chart data unavailable from OANDA.'}</p>}
        {chartQuery.data && <ForexChart data={chartQuery.data} onPriceSelect={setChartPrice} />}
      </section>

      <section className="content-grid">
        <div className="panel">
          <div className="panel-header compact"><div><p className="eyebrow">Post-trade / OANDA live</p><h3>{selectedPair} trade status</h3></div><span className="pill neutral">{trades.length} open</span></div>
          {positionsQuery.isLoading && <p>Loading open trades from OANDA…</p>}
          {positionsQuery.isError && <p role="alert">{positionsQuery.error instanceof Error ? positionsQuery.error.message : 'Could not load live OANDA trades.'}</p>}
          {!positionsQuery.isLoading && !positionsQuery.isError && !trades.length && <p>No open OANDA trade for {selectedPair}; live market data remains available.</p>}
          {trades.length > 0 && <>
            {trades.length > 1 && <label className="field-block"><span>Trade to control</span><select value={selectedTrade?.id ?? ''} onChange={(event) => setSelectedTradeId(event.target.value)}>{trades.map((trade) => <option key={trade.id} value={trade.id}>{trade.id} · {trade.side} · {trade.units} units</option>)}</select></label>}
            {selectedTrade && <div className="risk-summary">
              <div><span>Side / units</span><strong>{selectedTrade.side} · {Number(selectedTrade.units).toLocaleString()}</strong></div>
              <div><span>Entry / current</span><strong>{Number(selectedTrade.entryPrice).toFixed(precision)} / {selectedTrade.currentPrice ? Number(selectedTrade.currentPrice).toFixed(precision) : '—'}</strong></div>
              <div><span>Selected trade P/L</span><strong className={Number(selectedTrade.pnl) < 0 ? 'negative' : 'positive'}>{Number(selectedTrade.pnl) > 0 ? '+' : ''}{selectedTrade.pnl} {accountCurrency}</strong></div>
              <div><span>Pair net P/L</span><strong className={pairProfitLoss < 0 ? 'negative' : 'positive'}>{pairProfitLoss > 0 ? '+' : ''}{pairProfitLoss.toFixed(2)} {accountCurrency}</strong></div>
            </div>}
          </>}
          <div className="risk-summary">
            <div><span>OANDA bid / ask</span><strong>{quote ? `${Number(quote.bid).toFixed(precision)} / ${Number(quote.ask).toFixed(precision)}` : '—'}</strong></div>
            <div><span>Instrument pip</span><strong>{quote?.pipSize ? `${quote.pipSize} ${quote.quoteCurrency ?? ''}` : 'Unavailable'}</strong></div>
          </div>
          {quoteQuery.isError && <p role="alert">OANDA live quote unavailable; broker controls are disabled.</p>}
          {quote && <small>Quote updated {new Date(quote.time).toLocaleTimeString()} · {quote.environment}</small>}
        </div>

        <div className="panel">
          <div className="panel-header compact"><div><p className="eyebrow">Market range / OANDA</p><h3>Daily volatility estimate</h3></div><span className="pill neutral">D1 · 7 days</span></div>
          {volatilityQuery.isLoading && <p>Loading daily candles from OANDA…</p>}
          {volatilityQuery.isError && <p role="alert">{volatilityQuery.error instanceof Error ? volatilityQuery.error.message : 'Daily OANDA candles unavailable.'}</p>}
          {!volatilityQuery.isLoading && !volatilityQuery.isError && (Number.isFinite(volatilityPercent)
            ? <>
              <div
                className="volatility-range-visual"
                role="meter"
                aria-label={`${selectedPair} latest price relative to its seven-day high and low`}
                aria-valuemin={volatilityLow}
                aria-valuemax={volatilityHigh}
                aria-valuenow={Number.isFinite(latestPrice)
                  ? Math.min(volatilityHigh, Math.max(volatilityLow, latestPrice))
                  : undefined}
                aria-valuetext={`${Number.isFinite(latestPrice) ? latestPrice.toFixed(precision) : 'Price unavailable'}; ${priceRangeStatus}`}
              >
                <strong className="volatility-range-price" style={{ left: `${rangeLabelPosition}%` }}>
                  {Number.isFinite(latestPrice) ? latestPrice.toFixed(precision) : 'Price unavailable'}
                </strong>
                <div className="volatility-range-track">
                  <span className="volatility-range-midpoint" />
                  {Number.isFinite(latestPrice) && <span className="volatility-range-marker" style={{ left: `${rangeMarkerPosition}%` }} />}
                </div>
                <div className="volatility-range-labels">
                  <span>7-day low · {volatilityLow.toFixed(precision)}</span>
                  <span>7-day high · {volatilityHigh.toFixed(precision)}</span>
                </div>
                <small className={`volatility-range-status ${pricePositionPercent < 0 || pricePositionPercent > 100 ? 'outside' : ''}`}>
                  {priceRangeStatus}
                </small>
              </div>
              <div className="risk-summary"><div><span>High-low range</span><strong>{volatilityPercent.toFixed(2)}%</strong></div><div><span>Daily candles</span><strong>{volatilityCandles.length} / 7</strong></div></div>
            </>
            : <p>Not enough OANDA daily candles to calculate the 7-day range.</p>)}
        </div>
      </section>

      <section className="content-grid">
        <div className="panel">
          <div className="panel-header compact">
            <div><p className="eyebrow">Pre-trade</p><h3>Before order controls</h3></div>
            <div className="chart-controls">
              <span className="pill neutral">{form?.source ?? 'default-policy'}</span>
              <button
                type="button"
                className="primary-action"
                onClick={() => {
                  if (!form) return
                  setSaveFeedback(null)
                  saveMutation.mutate(form, {
                    onSuccess: () => setSaveFeedback('Risk policy saved and connected to automatic execution.'),
                    onError: () => setSaveFeedback('Risk policy was rejected by the backend.'),
                  })
                }}
                disabled={!form || saveMutation.isPending}
              >
                {saveMutation.isPending ? 'Saving…' : 'Save risk policy'}
              </button>
            </div>
          </div>
          {saveFeedback && <p className="risk-save-feedback" role="status">{saveFeedback}</p>}
          {form && <>
            <div className="settings-grid">
              <label className="field-block">
                <span>Maximum order units</span>
                <input
                  type="number"
                  min={quote?.minimumTradeSize ?? '1'}
                  step={quote?.tradeUnitsPrecision ? 'any' : '1'}
                  value={form.units}
                  onChange={(event) => update('units', event.target.value)}
                />
                <small className="calculated-price">An additional cap; risk sizing and OANDA limits may make the actual order smaller.</small>
              </label>
              <label className="field-block"><span>Risk budget</span><div className="value-mode"><input value={form.riskBudget} onChange={(event) => update('riskBudget', event.target.value)} /><select value={form.riskBudgetMode} onChange={(event) => update('riskBudgetMode', event.target.value as RiskConfig['riskBudgetMode'])}><option value="percent">%</option><option value="absolute">Amount</option></select></div></label>
              <label className="field-block"><span>Max exposure</span><div className="value-mode"><input value={form.maxExposure} onChange={(event) => update('maxExposure', event.target.value)} /><select value={form.maxExposureMode} onChange={(event) => update('maxExposureMode', event.target.value as RiskConfig['maxExposureMode'])}><option value="absolute">Amount</option><option value="percent">%</option></select></div></label>
              <label className="field-block"><span>Allowed side</span><select value={form.side} onChange={(event) => update('side', event.target.value as RiskConfig['side'])}><option value="NONE">NONE — no new entries</option><option value="LONG">LONG only</option><option value="SHORT">SHORT only</option><option value="BOTH">LONG and SHORT</option></select></label>
            </div>
            <p className="risk-helper">
              OANDA available units refresh from the broker quote. Automatic entries use the lowest of this
              value, your maximum units, risk budget, exposure limit, and visible market depth.
            </p>
            <div className="risk-summary">
              <div><span>OANDA available · LONG</span><strong>{availableUnits?.long ? Number(availableUnits.long).toLocaleString() : 'Unavailable'}</strong></div>
              <div><span>OANDA available · SHORT</span><strong>{availableUnits?.short ? Number(availableUnits.short).toLocaleString() : 'Unavailable'}</strong></div>
              <div><span>Broker margin rate</span><strong>{quote?.marginRate ? `${(Number(quote.marginRate) * 100).toFixed(2)}%` : 'Unavailable'}</strong><small>Set by OANDA for this account and instrument</small></div>
              <div><span>Margin available</span><strong>{quote?.marginAvailable ? `${Number(quote.marginAvailable).toLocaleString()} ${quote.accountCurrency ?? ''}` : 'Unavailable'}</strong></div>
            </div>
            <small className="calculated-price">
              Available units are indicative and can change before the order reaches OANDA
              {quote?.time ? ` · quote ${new Date(quote.time).toLocaleTimeString()}` : ''}.
              Leverage is determined by OANDA; it is not a per-order setting.
            </small>
          </>}
        </div>

        <div className="panel">
          <div className="panel-header compact"><div><p className="eyebrow">Post-trade</p><h3>After order controls</h3></div></div>
          {form && <>
            <div className="settings-grid">
              <label className="field-block"><span>Stop loss</span><div className="value-mode"><input value={form.stopLoss ?? ''} placeholder="Chart or value" onChange={(event) => update('stopLoss', event.target.value || null)} />{modeOptions(form.stopLossMode, (value) => update('stopLossMode', value))}</div>{renderValues(stopValues)}</label>
              <label className="field-block"><span>Take profit</span><div className="value-mode"><input value={form.takeProfit ?? ''} placeholder="Chart or value" onChange={(event) => update('takeProfit', event.target.value || null)} />{modeOptions(form.takeProfitMode, (value) => update('takeProfitMode', value))}</div>{renderValues(takeProfitValues)}</label>
              <label className="field-block"><span>Trailing stop loss (pips)</span><input type="number" min="0.1" step="0.1" value={trailingStopPips} onChange={(event) => setTrailingStopPips(event.target.value)} /><small className="calculated-price">Price distance: {trailingDistance ?? '—'} {quote?.quoteCurrency ?? ''} · 1 pip = {quote?.pipSize ?? '—'}</small></label>
              <label className="field-block"><span>Average-entry target</span><div className="value-mode"><input value={form.averageEntry ?? ''} placeholder="Chart or value" onChange={(event) => update('averageEntry', event.target.value || null)} />{modeOptions(form.averageEntryMode, (value) => update('averageEntryMode', value))}</div>{renderValues(averageValues)}</label>
              <label className="field-block"><span>Average-entry order units</span><input type="number" min="1" step="1" value={averageUnits} onChange={(event) => selectedTrade && setAverageUnitsByTrade((current) => ({ ...current, [selectedTrade.id]: event.target.value }))} /><small className="calculated-price">Suggested from the selected open trade; editable independently.</small></label>
              <label className="field-block"><span>Max averaging adds</span><input value={form.maxAdds} onChange={(event) => update('maxAdds', event.target.value)} /></label>
            </div>
            <div className="chart-controls risk-click-controls">
              <button type="button" onClick={() => {
                if (!selectedTrade) return
                if (trailingStopPips.trim() && stopLossConfigured) {
                  setExecutionFeedback({ message: 'Choose either a fixed stop-loss price or trailing stop, not both.', error: true })
                  return
                }
                if (trailingStopPips.trim() && !trailingDistance) {
                  setExecutionFeedback({ message: 'Enter a valid trailing-stop pip distance and wait for the live instrument pip size.', error: true })
                  return
                }
                if (stopLossInvalid || takeProfitInvalid) {
                  setExecutionFeedback({ message: 'Enter valid stop-loss and take-profit values in the selected units.', error: true })
                  return
                }
                if (!stopPrice && !takeProfitPrice && !trailingDistance) {
                  setExecutionFeedback({ message: 'Set a stop loss, take profit, or trailing stop before applying.', error: true })
                  return
                }
                setExecutionFeedback(null)
                protectionMutation.mutate({
                  tradeId: selectedTrade.id,
                  payload: {
                    stopLoss: trailingDistance ? null : stopPrice,
                    takeProfit: takeProfitPrice,
                    trailingStopLossDistance: trailingDistance,
                  },
                })
              }} disabled={!selectedTrade || !quote?.tradeable || protectionMutation.isPending}>{protectionMutation.isPending ? 'Updating OANDA…' : 'Apply protections on OANDA'}</button>
              <button type="button" onClick={() => {
                if (!selectedTrade || !averagePrice) {
                  setExecutionFeedback({ message: 'Select an open trade and enter a valid average-entry target.', error: true })
                  return
                }
                if (averageEntryInvalid || stopLossInvalid || takeProfitInvalid) {
                  setExecutionFeedback({ message: 'Enter valid average-entry, stop-loss, and take-profit values in the selected units.', error: true })
                  return
                }
                if (!/^[1-9]\d*$/.test(averageUnits)) {
                  setExecutionFeedback({ message: 'Average-entry order units must be a positive whole number.', error: true })
                  return
                }
                setExecutionFeedback(null)
                averageMutation.mutate({
                  tradeId: selectedTrade.id,
                  payload: {
                    units: averageUnits,
                    price: averagePrice,
                    stopLoss: stopPrice,
                    takeProfit: takeProfitPrice,
                  },
                })
              }} disabled={!selectedTrade || !quote?.tradeable || averageMutation.isPending}>{averageMutation.isPending ? 'Placing OANDA order…' : 'Place average-entry limit on OANDA'}</button>
            </div>
          </>}
          {executionFeedback && <p className="risk-save-feedback" role={executionFeedback.error ? 'alert' : 'status'}>{executionFeedback.message}</p>}
          {!selectedTrade && <p className="risk-helper">An open OANDA trade for this pair is required to apply protections or place an average-entry order.</p>}
        </div>
      </section>

      {configQuery.isError && <div className="panel page-panel"><p role="alert">Risk config could not be loaded from the backend.</p></div>}
      {isLoading && <div className="panel page-panel"><p>Loading risk data from backend…</p></div>}
      {isError && <div className="panel page-panel"><p>Backend risk endpoint unavailable; fallback data is being used.</p></div>}
      {!isLoading && data && <section className="content-grid"><div className="panel"><div className="panel-header compact"><div><p className="eyebrow">Policy</p><h3>Current limits</h3></div></div><div className="risk-summary"><div><span>Daily loss</span><strong>{data.dailyLoss}</strong></div><div><span>Max exposure</span><strong>{data.maxExposure}</strong></div><div><span>Margin level</span><strong>{data.marginLevel}</strong></div><div><span>Kill switch</span><strong>{data.killSwitch ? 'Active' : 'Clear'}</strong></div></div></div><div className="panel"><div className="panel-header compact"><div><p className="eyebrow">Exposure</p><h3>By symbol</h3></div></div><div className="strategy-stack">{data.exposureByPair.map((item) => <div key={item.pair} className="strategy-card"><div className="strategy-row"><strong>{item.pair}</strong><span className="pill neutral">{item.value}</span></div></div>)}</div></div></section>}
    </>
  )
}
