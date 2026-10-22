import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ForexChart } from '../components/ForexChart'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount, chartTimeframeFromStrategy } from '../components/chartOptions'
import {
  applyBrokerRiskProtection,
  createAverageEntryOrder,
  getBrokerPositions,
  getMarketQuote,
  getOrdersChart,
  getRiskConfig,
  getRiskSummary,
  getSetupStatus,
  saveRiskConfig,
  getStrategyReview,
  type LiveQuote,
  type RiskConfig,
  type StrategyReview,
} from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'
import { convertTrailingStopPipsToDistance } from '../utils/trailingStopLoss'

type ProtectionMode = 'price' | 'percent' | 'pips'
type ProtectedValue = 'stopLoss' | 'takeProfit' | 'averageEntry'
type ApprovedHyperopt = NonNullable<
  NonNullable<StrategyReview['approvedRevision']>['hyperopt']
>

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

function normalizePair(pair: string): string {
  return pair.replace(/_/g, '/').toUpperCase()
}

function normalizeTimeframe(timeframe: string): string {
  const normalized = timeframe.trim().toUpperCase()
  const unitFirst = normalized.match(/^([MHDW])(\d+)$/)
  return unitFirst ? `${unitFirst[2]}${unitFirst[1]}` : normalized
}

function hyperoptStopLossSummary(hyperopt: ApprovedHyperopt | null): string {
  const stopLoss = hyperopt?.stopLoss
  if (!stopLoss) return 'No stop-loss value is available in the approved Hyperopt result.'
  const unit = stopLoss.unit
    ?? (stopLoss.mode === 'pips' ? 'pips' : stopLoss.mode === 'percent' ? '% of entry price' : 'account currency')
  const kind = stopLoss.mode === 'money' ? 'maximum loss' : 'distance'
  const trailing = hyperopt.trailingStopLoss === true ? ' · trailing stop enabled' : ''
  return `Approved Hyperopt: ${stopLoss.value} ${unit} ${kind}${stopLoss.optimized ? ' · optimized' : ''}${trailing}`
}

function hyperoptRoiSummary(hyperopt: ApprovedHyperopt | null): string {
  const roi = hyperopt?.minimal_roi ?? hyperopt?.bestMinimalRoi
  if (!roi || !Object.keys(roi).length) {
    return 'No ROI exit schedule is available in the approved Hyperopt result.'
  }
  return `Approved Hyperopt ROI exits: ${Object.entries(roi)
    .sort(([left], [right]) => Number(left) - Number(right))
    .map(([minutes, rate]) => `${minutes} min: ${(rate * 100).toFixed(2)}%`)
    .join(' · ')}`
}

function hyperoptParametersSummary(hyperopt: ApprovedHyperopt | null): string {
  const parameters = hyperopt?.parameters ?? hyperopt?.bestParameters
  if (!parameters || !Object.keys(parameters).length) {
    return 'No strategy parameter overrides are available in the approved Hyperopt result.'
  }
  return Object.entries(parameters).map(([name, value]) => `${name} = ${value}`).join(' · ')
}

export function RiskPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery({ queryKey: ['risk'], queryFn: getRiskSummary })
  const availablePairs = useUiStore((state) => state.selectedInstruments)
  const [pair, setPair] = useState(availablePairs[0] ?? '')
  const selectedPair = availablePairs.length && !availablePairs.includes(pair) ? availablePairs[0] : pair
  const setupQuery = useQuery({ queryKey: ['setup-status'], queryFn: getSetupStatus })
  const selectedPairKey = selectedPair.replace(/\//g, '_').toUpperCase()
  const pairStrategy = setupQuery.data?.pairStrategies[selectedPairKey]
  const pairTimeframe = setupQuery.data?.pairTimeframes[selectedPairKey]
  const hyperoptReviewQuery = useQuery({
    queryKey: ['risk-hyperopt-review', selectedPair, pairStrategy, pairTimeframe],
    queryFn: () => {
      if (!pairStrategy || !pairTimeframe) {
        throw new Error(`No configured strategy and timeframe are available for ${selectedPair}.`)
      }
      return getStrategyReview(selectedPair, pairTimeframe, pairStrategy)
    },
    enabled: Boolean(pairStrategy && pairTimeframe),
  })
  const revision = hyperoptReviewQuery.data?.approvedRevision
  const revisionMatches = Boolean(revision
    && normalizePair(revision.pair) === normalizePair(selectedPair)
    && revision.strategyClass === pairStrategy
    && normalizeTimeframe(revision.timeframe) === normalizeTimeframe(pairTimeframe ?? ''))
  const approvedHyperopt = revisionMatches && revision ? revision.hyperopt ?? null : null
  const [timeframeOverride, setTimeframeOverride] = useState<string | null>(null)
  const timeframe = timeframeOverride
    ?? chartTimeframeFromStrategy(pairTimeframe, 'M15')
  const [countMultiplier, setCountMultiplier] = useState(1)
  const configQuery = useQuery({ queryKey: ['risk-config', selectedPair], queryFn: () => getRiskConfig(selectedPair), enabled: Boolean(selectedPair) })
  const chartQuery = useQuery({
    queryKey: ['risk-chart', selectedPair, timeframe, countMultiplier],
    queryFn: () => getOrdersChart(selectedPair, timeframe, chartCandleCount(countMultiplier)),
    refetchInterval: 30000,
    enabled: !setupQuery.isLoading && Boolean(selectedPair),
  })
  const volatilityQuery = useQuery({
    queryKey: ['risk-volatility', selectedPair],
    queryFn: () => getOrdersChart(selectedPair, 'D', 30),
    refetchInterval: 300000,
    enabled: Boolean(selectedPair),
  })
  const quoteQuery = useQuery({
    queryKey: ['risk-live-quote', selectedPair],
    queryFn: () => getMarketQuote(selectedPair),
    refetchInterval: 3000,
    retry: false,
    enabled: Boolean(selectedPair),
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
      payload: Partial<{ stopLoss: string; takeProfit: string; trailingStopLossDistance: string }>
    }) => applyBrokerRiskProtection(tradeId, payload),
    onSuccess: async () => {
      setExecutionFeedback({ message: 'Protection update sent to OANDA.', error: false, kind: 'protection' })
      await queryClient.invalidateQueries({ queryKey: ['broker-positions'] })
    },
    onError: (error) => setExecutionFeedback({
      message: error instanceof Error ? error.message : 'OANDA protection update failed.',
      error: true,
      kind: 'protection',
    }),
  })
  const averageMutation = useMutation({
    mutationFn: ({ tradeId, payload }: {
      tradeId: string
      payload: { units: string; price: string; stopLoss?: string; takeProfit?: string }
    }) => createAverageEntryOrder(tradeId, payload),
    onSuccess: async () => {
      setExecutionFeedback({
        message: 'Average-entry limit order placed at OANDA; it will be cancelled if its parent trade closes.',
        error: false,
        kind: 'average',
      })
      await queryClient.invalidateQueries({ queryKey: ['broker-pending-orders'] })
    },
    onError: (error) => setExecutionFeedback({
      message: error instanceof Error ? error.message : 'OANDA average-entry order failed.',
      error: true,
      kind: 'average',
    }),
  })
  const [selection, setSelection] = useState<ProtectedValue>('stopLoss')
  const [selectedTradeId, setSelectedTradeId] = useState('')
  const [averageUnitsByTrade, setAverageUnitsByTrade] = useState<Record<string, string>>({})
  const [trailingStopPips, setTrailingStopPips] = useState('')
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null)
  const [executionFeedback, setExecutionFeedback] = useState<{
    message: string
    error: boolean
    kind: 'protection' | 'average'
  } | null>(null)

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
  const availableLongUnits = Number(availableUnits?.long)
  const availableShortUnits = Number(availableUnits?.short)
  const pairLongUnits = trades
    .filter((trade) => trade.side === 'BUY')
    .reduce((total, trade) => total + Math.abs(Number(trade.units)), 0)
  const pairShortUnits = trades
    .filter((trade) => trade.side === 'SELL')
    .reduce((total, trade) => total + Math.abs(Number(trade.units)), 0)
  const pairUsedUnits = pairLongUnits + pairShortUnits
  const pairVolumeBreakdownAvailable = !positionsQuery.isLoading
    && !positionsQuery.isError
    && Number.isFinite(availableLongUnits) && availableLongUnits >= 0
    && Number.isFinite(availableShortUnits) && availableShortUnits >= 0
    && Number.isFinite(pairUsedUnits) && pairUsedUnits >= 0
  const pairRemainingUnits = pairVolumeBreakdownAvailable
    ? availableLongUnits + availableShortUnits
    : 0
  const pairTotalCapacityUnits = pairUsedUnits + pairRemainingUnits
  const pairUsedPercent = pairVolumeBreakdownAvailable && pairTotalCapacityUnits > 0
    ? pairUsedUnits / pairTotalCapacityUnits * 100
    : 0
  const pairVolumeDonutStyle = {
    background: `conic-gradient(#fb7185 0 ${pairUsedPercent}%, #60a5fa ${pairUsedPercent}% 100%)`,
  }
  const liveUnitsLimit = Math.max(
    Number.isFinite(availableLongUnits) ? availableLongUnits : 0,
    Number.isFinite(availableShortUnits) ? availableShortUnits : 0,
  )
  const liveMarginLimitGbp = Number(quote?.marginAvailableGbp)
  const marginAvailableValue = quote?.marginAvailable !== undefined && quote.marginAvailable !== null
    ? Number(quote.marginAvailable)
    : Number.NaN
  const marginUsedValue = quote?.marginUsed !== undefined && quote.marginUsed !== null
    ? Number(quote.marginUsed)
    : Number.NaN
  const marginTotal = marginAvailableValue + marginUsedValue
  const marginBreakdownAvailable = Number.isFinite(marginAvailableValue) && marginAvailableValue >= 0
    && Number.isFinite(marginUsedValue) && marginUsedValue >= 0
    && marginTotal > 0
  const marginUsedPercent = marginBreakdownAvailable ? marginUsedValue / marginTotal * 100 : 0
  const marginDonutStyle = {
    background: `conic-gradient(#fb7185 0 ${marginUsedPercent}%, #34d399 ${marginUsedPercent}% 100%)`,
  }
  const exposureSliderMaximum = form?.maxExposureMode === 'units'
    ? liveUnitsLimit
    : form?.maxExposureMode === 'margin_gbp'
      ? liveMarginLimitGbp
      : form?.maxExposureMode === 'margin_percent'
        ? 100
        : 0
  const exposureSliderStep = form?.maxExposureMode === 'margin_percent'
    ? 1
    : form?.maxExposureMode === 'margin_gbp'
      ? Math.max(exposureSliderMaximum / 200, 0.01)
      : 1
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
  const getTradeActionDisabledMessage = (isPending: boolean): string | null => {
    if (!selectedTrade) {
      if (positionsQuery.isLoading) return 'در حال دریافت معاملات باز از OANDA؛ پس از بارگذاری، معامله‌ای برای این نماد انتخاب کنید.'
      if (positionsQuery.isError) return 'دریافت معاملات باز از OANDA ناموفق بود؛ اتصال را بررسی و صفحه را دوباره بارگذاری کنید.'
      return `برای استفاده از این دکمه، ابتدا باید یک معاملهٔ باز OANDA برای ${selectedPair} داشته باشید.`
    }
    if (!quote) {
      if (quoteQuery.isLoading) return 'در انتظار دریافت قیمت زنده از OANDA؛ دکمه پس از دریافت قیمت فعال می‌شود.'
      return 'قیمت زندهٔ OANDA در دسترس نیست؛ اتصال بروکر را بررسی کنید.'
    }
    if (!quote.tradeable) return `OANDA در حال حاضر ${selectedPair} را غیرقابل‌معامله اعلام کرده است؛ ممکن است بازار بسته یا متوقف باشد.`
    if (isPending) return 'درخواست در حال ارسال به OANDA است؛ لطفاً صبر کنید.'
    return null
  }
  const averageActionDisabledMessage = getTradeActionDisabledMessage(averageMutation.isPending)
  const protectionActionDisabledMessage = getTradeActionDisabledMessage(protectionMutation.isPending)

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
        <div className="panel-header compact"><div><p className="eyebrow">Risk map</p><h3>Price-based protection</h3></div><div className="chart-controls"><select aria-label="Risk pair" value={selectedPair} onChange={(event) => setPair(event.target.value)}>{availablePairs.length ? availablePairs.map((item) => <option key={item} value={item}>{item}</option>) : <option value="" disabled>No configured pairs</option>}</select><ChartDataControls timeframe={timeframe} countMultiplier={countMultiplier} onTimeframeChange={setTimeframeOverride} onCountMultiplierChange={setCountMultiplier} /></div></div>
        {!availablePairs.length && <p className="risk-helper">No trading pairs are configured. Add at least one pair in Setup to view risk controls.</p>}
        <p className="risk-helper">Select a protection level, then click the chart. Prices and trade status below are refreshed from OANDA.</p>
        <div className="chart-controls risk-click-controls"><button type="button" className={selection === 'stopLoss' ? 'selected' : ''} onClick={() => setSelection('stopLoss')}>Set stop loss</button><button type="button" className={selection === 'takeProfit' ? 'selected' : ''} onClick={() => setSelection('takeProfit')}>Set take profit</button><button type="button" className={selection === 'averageEntry' ? 'selected' : ''} onClick={() => setSelection('averageEntry')}>Set average entry</button></div>
        {chartQuery.isLoading && <p>Loading OANDA chart…</p>}
        {chartQuery.isError && <p role="alert">{chartQuery.error instanceof Error ? chartQuery.error.message : 'Risk chart data unavailable from OANDA.'}</p>}
        {chartQuery.data && <ForexChart data={chartQuery.data} onPriceSelect={setChartPrice} />}
      </section>

      <section className="content-grid risk-parent-grid">
        <div className="panel risk-parent-panel risk-parent-panel--policy">
          <div className="panel-header compact"><div><p className="eyebrow">🧭 TRADE WORKFLOW</p><h3>Position &amp; policy</h3></div></div>
          <div className="risk-parent-stack">
            <div className="panel risk-child-panel risk-child-panel--trade">
              <div className="panel-header compact"><div><p className="eyebrow">💹 POST-TRADE · OANDA LIVE</p><h3>{selectedPair} trade status</h3></div><span className={`pill ${trades.length ? 'risk-trade-open-pill' : 'neutral'}`}>{trades.length ? '🟢' : '💤'} {trades.length} open</span></div>
              {positionsQuery.isLoading && <p className="risk-trade-status"><span aria-hidden="true">⏳</span> Loading open trades from OANDA…</p>}
              {positionsQuery.isError && <p className="risk-trade-status risk-trade-status--error" role="alert"><span aria-hidden="true">⚠️</span> {positionsQuery.error instanceof Error ? positionsQuery.error.message : 'Could not load live OANDA trades.'}</p>}
              {!positionsQuery.isLoading && !positionsQuery.isError && !trades.length && <p className="risk-trade-status"><span aria-hidden="true">📭</span> No open OANDA trade for {selectedPair}; live market data remains available.</p>}
              {trades.length > 0 && <>
                {trades.length > 1 && <label className="field-block"><span>🎯 Trade to control</span><select value={selectedTrade?.id ?? ''} onChange={(event) => setSelectedTradeId(event.target.value)}>{trades.map((trade) => <option key={trade.id} value={trade.id}>{trade.id} · {trade.side === 'BUY' ? '🟢 LONG' : '🔴 SHORT'} · {trade.units} units</option>)}</select></label>}
                {selectedTrade && <div className="risk-summary">
                  <div><span>↕️ Side / units</span><strong>{selectedTrade.side === 'BUY' ? '🟢 LONG' : '🔴 SHORT'} · {Number(selectedTrade.units).toLocaleString()}</strong></div>
                  <div><span>🎯 Entry / current</span><strong>{Number(selectedTrade.entryPrice).toFixed(precision)} / {selectedTrade.currentPrice ? Number(selectedTrade.currentPrice).toFixed(precision) : '—'}</strong></div>
                  <div><span>{Number(selectedTrade.pnl) > 0 ? '📈' : Number(selectedTrade.pnl) < 0 ? '📉' : '➖'} Selected trade P/L</span><strong className={Number(selectedTrade.pnl) < 0 ? 'negative' : Number(selectedTrade.pnl) > 0 ? 'positive' : 'risk-pnl-neutral'}>{Number(selectedTrade.pnl) > 0 ? '+' : ''}{selectedTrade.pnl} {accountCurrency}</strong></div>
                  <div><span>{pairProfitLoss > 0 ? '📈' : pairProfitLoss < 0 ? '📉' : '➖'} Pair net P/L</span><strong className={pairProfitLoss < 0 ? 'negative' : pairProfitLoss > 0 ? 'positive' : 'risk-pnl-neutral'}>{pairProfitLoss > 0 ? '+' : ''}{pairProfitLoss.toFixed(2)} {accountCurrency}</strong></div>
                </div>}
              </>}
              <div className="risk-summary">
                <div><span>💱 OANDA bid / ask</span><strong>{quote ? `${Number(quote.bid).toFixed(precision)} / ${Number(quote.ask).toFixed(precision)}` : '—'}</strong></div>
                <div><span>📏 Instrument pip</span><strong>{quote?.pipSize ? `${quote.pipSize} ${quote.quoteCurrency ?? ''}` : 'Unavailable'}</strong></div>
              </div>
              {quoteQuery.isError && <p className="risk-trade-status risk-trade-status--error" role="alert"><span aria-hidden="true">⚠️</span> OANDA live quote unavailable; broker controls are disabled.</p>}
              {quote && <small className="risk-trade-updated">🟢 Quote updated {new Date(quote.time).toLocaleTimeString()} · {quote.environment}</small>}
              <section className="risk-pair-volume" aria-label={`${selectedPair} trade volume`}>
                <div className="risk-pair-volume-heading">
                  <div><span>📊 {selectedPair} volume usage</span><small>Open pair volume compared with remaining OANDA capacity</small></div>
                  {pairVolumeBreakdownAvailable && pairTotalCapacityUnits > 0 && <strong>{pairUsedPercent.toFixed(1)}% used</strong>}
                </div>
                {pairVolumeBreakdownAvailable && pairTotalCapacityUnits > 0 ? (
                  <div className="risk-pair-volume-content">
                    <div
                      className="risk-pair-volume-donut"
                      style={pairVolumeDonutStyle}
                      role="img"
                      aria-label={`${selectedPair}: ${pairUsedUnits.toLocaleString()} units used in open trades; ${pairRemainingUnits.toLocaleString()} units remaining across OANDA long and short capacity`}
                    >
                      <div className="risk-pair-volume-donut-center">
                        <strong>{pairUsedPercent.toFixed(0)}%</strong>
                        <small>used</small>
                      </div>
                    </div>
                    <div className="risk-pair-volume-legend">
                      <div><span className="risk-pair-volume-dot used" /><span>🔴 Open trade volume</span><strong>{pairUsedUnits.toLocaleString()} units</strong></div>
                      <div><span className="risk-pair-volume-dot remaining" /><span>🔵 Remaining OANDA capacity</span><strong>{pairRemainingUnits.toLocaleString()} units</strong></div>
                    </div>
                  </div>
                ) : <p className="risk-trade-status">{positionsQuery.isLoading
                  ? '⏳ Waiting for open trades and OANDA unit limits…'
                  : positionsQuery.isError
                    ? '⚠️ Open trade data is unavailable; volume usage cannot be calculated.'
                    : !quote
                      ? '⏳ Waiting for live OANDA unit limits…'
                      : pairVolumeBreakdownAvailable
                        ? 'ℹ️ OANDA reports no remaining unit capacity for this pair.'
                        : 'ℹ️ OANDA did not return complete LONG/SHORT unit limits; volume usage cannot be calculated.'}</p>}
              </section>
            </div>

            <div className="panel risk-controls-panel risk-child-panel risk-child-panel--policy">
          <div className="panel-header compact">
            <div><p className="eyebrow">🛡️ PRE-TRADE · PRIORITY POLICY</p><h3>Before order controls</h3><p className="risk-panel-intro">Saved here, these controls take priority over strategy risk settings on every automatic order sent to OANDA.</p></div>
            <span className="pill neutral">🛡️ {form?.source ?? 'default-policy'}</span>
          </div>
          {form && <>
            <div className="settings-grid risk-settings-grid">
              <label className="field-block">
                <span>Max exposure</span>
                <div className="value-mode">
                  <input type="number" min="0.01" max={form.maxExposureMode === 'margin_percent' ? '100' : undefined} step="any" inputMode="decimal" value={form.maxExposure} onChange={(event) => update('maxExposure', event.target.value)} />
                  <select aria-label="Maximum exposure mode" value={form.maxExposureMode} onChange={(event) => update('maxExposureMode', event.target.value as RiskConfig['maxExposureMode'])}>
                    <option value="margin_gbp">GBP margin</option>
                    <option value="margin_percent">% available margin</option>
                    <option value="units">Units</option>
                    {(form.maxExposureMode === 'absolute' || form.maxExposureMode === 'percent') && <option value={form.maxExposureMode}>Legacy notional cap</option>}
                  </select>
                </div>
                <small className="risk-field-help">🔒 This is a hard ceiling applied after risk sizing; OANDA available units and market depth can reduce it further.</small>
              </label>
              <div className="field-block risk-exposure-slider">
                <div className="risk-slider-heading"><span>Live OANDA limit selector</span><strong>{exposureSliderMaximum > 0 ? `${Math.min(Number(form.maxExposure), exposureSliderMaximum).toLocaleString(undefined, { maximumFractionDigits: 2 })} / ${exposureSliderMaximum.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${form.maxExposureMode === 'units' ? 'units' : form.maxExposureMode === 'margin_gbp' ? 'GBP' : '%'}` : 'Live limit unavailable'}</strong></div>
                <input
                  type="range"
                  aria-label="Maximum exposure from live OANDA limits"
                  min="0"
                  max={exposureSliderMaximum > 0 ? exposureSliderMaximum : 100}
                  step={exposureSliderStep}
                  value={Math.min(Number(form.maxExposure) || 0, exposureSliderMaximum > 0 ? exposureSliderMaximum : 0)}
                  disabled={exposureSliderMaximum <= 0}
                  onChange={(event) => update('maxExposure', event.target.value)}
                />
                <div className="risk-slider-labels"><small>0</small><small>{form.maxExposureMode === 'units' ? `${liveUnitsLimit.toLocaleString()} units available` : form.maxExposureMode === 'margin_gbp' ? `£${Number.isFinite(liveMarginLimitGbp) ? liveMarginLimitGbp.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'} available` : form.maxExposureMode === 'margin_percent' ? '100% available margin' : 'Select one of the live modes to use the slider'}</small></div>
              </div>
              <fieldset className="risk-side-field">
                <legend>Allowed side</legend>
                <div className="risk-side-options" role="radiogroup" aria-label="Allowed side">
                  {([
                    { value: 'BOTH', label: 'Both directions', icon: '↕️', detail: 'LONG + SHORT' },
                    { value: 'LONG', label: 'Long only', icon: '🟢', detail: 'Buy / LONG' },
                    { value: 'SHORT', label: 'Short only', icon: '🔴', detail: 'Sell / SHORT' },
                    { value: 'NONE', label: 'Pause entries', icon: '⏸️', detail: 'No new trades' },
                  ] as const).map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      role="radio"
                      aria-checked={form.side === option.value}
                      className={`risk-side-option ${form.side === option.value ? 'selected' : ''} risk-side-option--${option.value.toLowerCase()}`}
                      onClick={() => update('side', option.value)}
                    >
                      <span className="risk-side-icon" aria-hidden="true">{option.icon}</span>
                      <span className="risk-side-label">{option.label}</span>
                      <small>{option.detail}</small>
                    </button>
                  ))}
                </div>
              </fieldset>
            </div>
            <p className="risk-helper">ℹ️ Position sizing uses the risk fraction already configured in Setup. These controls independently enforce the exposure, direction, and protection limits before an automatic order reaches OANDA.</p>
            <section className="risk-margin-panel" aria-label="OANDA margin usage">
              <div className="risk-margin-heading">
                <div><span>Account margin</span><small>Live OANDA account · {quote?.accountCurrency ?? '—'}</small></div>
                {marginBreakdownAvailable && <strong>{marginUsedPercent.toFixed(1)}% used</strong>}
              </div>
              {marginBreakdownAvailable ? (
                <div className="risk-margin-breakdown">
                  <div
                    className="risk-margin-donut"
                    style={marginDonutStyle}
                    role="img"
                    aria-label={`Margin used ${marginUsedValue.toLocaleString()} ${quote?.accountCurrency ?? ''}; remaining ${marginAvailableValue.toLocaleString()} ${quote?.accountCurrency ?? ''}`}
                  >
                    <div className="risk-margin-donut-center">
                      <strong>{marginUsedPercent.toFixed(0)}%</strong>
                      <small>used</small>
                    </div>
                  </div>
                  <div className="risk-margin-legend">
                    <div><span className="risk-margin-dot used" /><span>Used</span><strong>{marginUsedValue.toLocaleString(undefined, { maximumFractionDigits: 2 })} {quote?.accountCurrency ?? ''}</strong></div>
                    <div><span className="risk-margin-dot remaining" /><span>Remaining</span><strong>{marginAvailableValue.toLocaleString(undefined, { maximumFractionDigits: 2 })} {quote?.accountCurrency ?? ''}</strong></div>
                  </div>
                </div>
              ) : <p className="risk-helper">OANDA margin-used and available values are not both available; the usage chart cannot be calculated.</p>}
            </section>
            <small className="calculated-price">
              Available units and margin are indicative and can change before the order reaches OANDA
              {quote?.time ? ` · quote ${new Date(quote.time).toLocaleTimeString()}` : ''}.
              The % mode caps the share of current available margin; GBP mode limits required margin. Leverage is determined by OANDA.
            </small>
          </>}
          {saveFeedback && <p className="risk-save-feedback" role="status">{saveFeedback}</p>}
          <div className="risk-action-row">
            <button
              type="button"
              className="risk-action-button"
              onClick={() => {
                if (!form) return
                setSaveFeedback(null)
                saveMutation.mutate(form, {
                  onSuccess: () => setSaveFeedback('Risk policy saved and connected to automatic execution.'),
                  onError: (error) => setSaveFeedback(error instanceof Error ? error.message : 'Risk policy was rejected by the backend.'),
                })
              }}
              disabled={!form || saveMutation.isPending}
            >
              {saveMutation.isPending ? '⏳ Saving…' : '💾 Save risk policy'}
            </button>
          </div>
              </div>
          </div>
        </div>

        <div className="panel risk-parent-panel risk-parent-panel--market">
          <div className="panel-header compact"><div><p className="eyebrow">📊 MARKET &amp; EXECUTION</p><h3>Market data &amp; trade actions</h3></div></div>
          <div className="risk-parent-stack">
              <div className="panel risk-child-panel risk-child-panel--market">
                <div className="panel-header compact"><div><p className="eyebrow">🌡️ MARKET RANGE · OANDA</p><h3>Daily volatility estimate</h3></div><span className="pill neutral">D1 · 7 days</span></div>
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

              <div className="panel risk-child-panel risk-child-panel--average">
                <div className="panel-header compact">
                  <div><p className="eyebrow">➕ POST-TRADE · AVERAGE ENTRY</p><h3>Place average-entry limit on OANDA</h3></div>
                  <span className="pill neutral">Separate order</span>
                </div>
                <p className="risk-helper">This creates a separate linked limit order; only populated stop-loss and take-profit values are attached to it.</p>
                {form && <>
                  <div className="settings-grid">
                    <label className="field-block">
                      <span>Average-entry target</span>
                      <div className="value-mode"><input value={form.averageEntry ?? ''} placeholder="Chart or value" onChange={(event) => update('averageEntry', event.target.value || null)} />{modeOptions(form.averageEntryMode, (value) => update('averageEntryMode', value))}</div>
                      {renderValues(averageValues)}
                      <small className="risk-hyperopt-reference">Average-entry targets are not tuned by Hyperopt for this pair.</small>
                    </label>
                    <label className="field-block">
                      <span>Average-entry order units</span>
                      <input type="number" min="1" step="1" value={averageUnits} onChange={(event) => selectedTrade && setAverageUnitsByTrade((current) => ({ ...current, [selectedTrade.id]: event.target.value }))} />
                      <small className="calculated-price">Suggested from the selected open trade; editable independently.</small>
                    </label>
                    <label className="field-block">
                      <span>Max averaging adds</span>
                      <input value={form.maxAdds} onChange={(event) => update('maxAdds', event.target.value)} />
                      <small className="risk-hyperopt-reference">Averaging-add limits are not tuned by Hyperopt.</small>
                    </label>
                  </div>
                  <div className="risk-action-row">
                    <button type="button" onClick={() => {
                      if (!selectedTrade || !averagePrice) {
                        setExecutionFeedback({ message: 'Select an open trade and enter a valid average-entry target.', error: true, kind: 'average' })
                        return
                      }
                      if (averageEntryInvalid || stopLossInvalid || takeProfitInvalid) {
                        setExecutionFeedback({ message: 'Enter valid average-entry, stop-loss, and take-profit values in the selected units.', error: true, kind: 'average' })
                        return
                      }
                      if (!/^[1-9]\d*$/.test(averageUnits)) {
                        setExecutionFeedback({ message: 'Average-entry order units must be a positive whole number.', error: true, kind: 'average' })
                        return
                      }
                      setExecutionFeedback(null)
                      averageMutation.mutate({
                        tradeId: selectedTrade.id,
                        payload: {
                          units: averageUnits,
                          price: averagePrice,
                          ...(stopPrice ? { stopLoss: stopPrice } : {}),
                          ...(takeProfitPrice ? { takeProfit: takeProfitPrice } : {}),
                        },
                      })
                    }} className="risk-action-button" disabled={!selectedTrade || !quote?.tradeable || averageMutation.isPending}>{averageMutation.isPending ? '⏳ Placing order…' : '➕ Place average-entry order'}</button>
                  </div>
                  {averageActionDisabledMessage && <p className="risk-action-prerequisite" role="status">{averageActionDisabledMessage}</p>}
                </>}
                {executionFeedback?.kind === 'average' && <p className="risk-save-feedback" role={executionFeedback.error ? 'alert' : 'status'}>{executionFeedback.message}</p>}
              </div>

              <div className="panel risk-child-panel risk-child-panel--protection">
          <div className="panel-header compact">
              <div><p className="eyebrow">🔐 POST-TRADE · LIVE PROTECTION</p><h3>Apply protections on OANDA</h3></div>
              <span className="pill neutral">{selectedTrade ? `${selectedTrade.side} · ${selectedTrade.id}` : 'No open trade'}</span>
          </div>
          <p className="risk-helper">Only protection fields with values are sent to the selected open trade. Blank fields are left unchanged.</p>
          {setupQuery.isError && <p className="risk-helper" role="alert">Could not load the configured strategy for Hyperopt comparison: {setupQuery.error instanceof Error ? setupQuery.error.message : 'setup unavailable'}</p>}
          {hyperoptReviewQuery.isError && <p className="risk-helper" role="alert">Could not load approved Hyperopt values: {hyperoptReviewQuery.error instanceof Error ? hyperoptReviewQuery.error.message : 'review unavailable'}</p>}
          {revision && !revisionMatches && <p className="risk-helper">The approved Hyperopt result does not match the currently configured strategy and timeframe for {selectedPair}.</p>}
          {revisionMatches && !approvedHyperopt && <p className="risk-helper">The approved strategy revision has no Hyperopt values available for {selectedPair}.</p>}
          {!revision && !hyperoptReviewQuery.isLoading && !hyperoptReviewQuery.isError && pairStrategy && pairTimeframe && <p className="risk-helper">No approved Hyperopt result is available for {selectedPair}.</p>}
          {approvedHyperopt && <details className="risk-hyperopt-summary">
            <summary>Approved strategy parameters used for this pair</summary>
            <p>{hyperoptParametersSummary(approvedHyperopt)}</p>
          </details>}
          {form && <>
            <div className="settings-grid">
              <label className="field-block">
                <span>Stop loss</span>
                <div className="value-mode"><input value={form.stopLoss ?? ''} placeholder="Chart or value" onChange={(event) => update('stopLoss', event.target.value || null)} />{modeOptions(form.stopLossMode, (value) => update('stopLossMode', value))}</div>
                {renderValues(stopValues)}
                {form.stopLossSource === 'approved-hyperopt' && (
                  <small className="risk-field-help">
                    Using the approved Hyperopt stop loss by default. Changing this value and saving creates a per-pair Risk controls override.
                  </small>
                )}
                {form.stopLossIssue && <small className="inline-error" role="alert">{form.stopLossIssue}</small>}
                <small className="risk-hyperopt-reference">{hyperoptStopLossSummary(approvedHyperopt)}</small>
              </label>
              <label className="field-block">
                <span>Take profit</span>
                <div className="value-mode"><input value={form.takeProfit ?? ''} placeholder="Chart or value" onChange={(event) => update('takeProfit', event.target.value || null)} />{modeOptions(form.takeProfitMode, (value) => update('takeProfitMode', value))}</div>
                {renderValues(takeProfitValues)}
                <small className="risk-hyperopt-reference">{hyperoptRoiSummary(approvedHyperopt)}</small>
                <small className="risk-field-help">{form.takeProfit?.trim()
                  ? 'With a fixed take profit, the OANDA target handles exits; strategy-exit signals and Hyperopt ROI are ignored. Opposite entry signals can still reverse a position.'
                  : 'Without a fixed take profit, automatic strategy-exit signals and Hyperopt ROI exits remain active.'}</small>
              </label>
              <label className="field-block">
                <span>Trailing stop loss (pips)</span>
                <input type="number" min="0.1" step="0.1" value={trailingStopPips} onChange={(event) => setTrailingStopPips(event.target.value)} />
                <small className="calculated-price">Price distance: {trailingDistance ?? '—'} {quote?.quoteCurrency ?? ''} · 1 pip = {quote?.pipSize ?? '—'}</small>
                <small className="risk-hyperopt-reference">Approved Hyperopt trailing stop: {approvedHyperopt?.trailingStopLoss === true ? 'enabled' : approvedHyperopt?.trailingStopLoss === false ? 'disabled' : 'not specified'}{approvedHyperopt?.stopLoss ? ` · distance: ${approvedHyperopt.stopLoss.value} ${approvedHyperopt.stopLoss.unit ?? approvedHyperopt.stopLoss.mode}` : ''}</small>
              </label>
            </div>
            <div className="risk-action-row">
              <button type="button" onClick={() => {
                if (!selectedTrade) return
                if (trailingStopPips.trim() && stopLossConfigured) {
                  setExecutionFeedback({ message: 'Choose either a fixed stop-loss price or trailing stop, not both.', error: true, kind: 'protection' })
                  return
                }
                if (trailingStopPips.trim() && !trailingDistance) {
                  setExecutionFeedback({ message: 'Enter a valid trailing-stop pip distance and wait for the live instrument pip size.', error: true, kind: 'protection' })
                  return
                }
                if (stopLossInvalid || takeProfitInvalid) {
                  setExecutionFeedback({ message: 'Enter valid stop-loss and take-profit values in the selected units.', error: true, kind: 'protection' })
                  return
                }
                if (!stopPrice && !takeProfitPrice && !trailingDistance) {
                  setExecutionFeedback({ message: 'Set a stop loss, take profit, or trailing stop before applying.', error: true, kind: 'protection' })
                  return
                }
                setExecutionFeedback(null)
                protectionMutation.mutate({
                  tradeId: selectedTrade.id,
                  payload: {
                    ...(!trailingDistance && stopPrice ? { stopLoss: stopPrice } : {}),
                    ...(takeProfitPrice ? { takeProfit: takeProfitPrice } : {}),
                    ...(trailingDistance ? { trailingStopLossDistance: trailingDistance } : {}),
                  },
                })
              }} className="risk-action-button" disabled={!selectedTrade || !quote?.tradeable || protectionMutation.isPending}>{protectionMutation.isPending ? '⏳ Updating OANDA…' : '🔐 Apply protections on OANDA'}</button>
            </div>
            {protectionActionDisabledMessage && <p className="risk-action-prerequisite" role="status">{protectionActionDisabledMessage}</p>}
          </>}
          {executionFeedback?.kind === 'protection' && <p className="risk-save-feedback" role={executionFeedback.error ? 'alert' : 'status'}>{executionFeedback.message}</p>}
            </div>
          </div>
        </div>
      </section>

      {configQuery.isError && <div className="panel page-panel"><p role="alert">Risk config could not be loaded from the backend.</p></div>}
      {isLoading && <div className="panel page-panel"><p>Loading risk data from backend…</p></div>}
      {isError && <div className="panel page-panel"><p>Backend risk endpoint unavailable; fallback data is being used.</p></div>}
    </>
  )
}
