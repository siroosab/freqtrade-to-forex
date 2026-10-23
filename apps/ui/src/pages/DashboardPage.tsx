import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { ForexChart } from '../components/ForexChart'
import { ChartDataControls } from '../components/ChartDataControls'
import { chartCandleCount, chartTimeframeFromStrategy } from '../components/chartOptions'
import { closeManualPosition, createManualClientOrderId, getAccountSummary, getAutoExecutionStatus, getBrokerPendingOrders, getBrokerPositions, getMarketQuote, getMarketStatus, getMarketSummary, getOrdersChart, getRiskSummary, getSetupStatus, modifyManualPosition, modifyPendingOrder, setAutoExecution, submitLimitOrder, submitMarketOrder, type BrokerPendingOrder, type BrokerTrade } from '../api/mockApi'
import { convertTrailingStopPipsToDistance, validateTrailingStopInput } from '../utils/trailingStopLoss'
import { useForexSocket } from '../hooks/useForexSocket'
import { useUiStore } from '../store/useUiStore'

function formatMarketCountdown(transitionAt: string | null | undefined, now: number): string | null {
  if (!transitionAt) return null
  const remaining = Date.parse(transitionAt) - now
  if (!Number.isFinite(remaining) || remaining < 0) return null
  const totalMinutes = Math.ceil(remaining / 60_000)
  const days = Math.floor(totalMinutes / 1440)
  const hours = Math.floor((totalMinutes % 1440) / 60)
  const minutes = totalMinutes % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

function formatExecutionResultStatus(status: string): { label: string; icon: string; tone: string } {
  const normalized = status.toLowerCase()
  const knownStatuses: Record<string, { label: string; icon: string; tone: string }> = {
    filled: { label: 'Order filled', icon: '✅', tone: 'success' },
    closed: { label: 'Position closed', icon: '✅', tone: 'success' },
    trailing_activated: { label: 'Trailing stop active', icon: '🛡️', tone: 'success' },
    trailing_updated: { label: 'Trailing stop tightened', icon: '🔄', tone: 'success' },
    blocked: { label: 'Entry blocked', icon: '⛔', tone: 'warning' },
    not_filled: { label: 'Order not filled', icon: '⏳', tone: 'warning' },
    close_not_filled: { label: 'Close not filled', icon: '⏳', tone: 'warning' },
    trailing_update_failed: { label: 'Trailing stop update failed', icon: '⚠️', tone: 'error' },
    already_processed: { label: 'Already processed', icon: '🔁', tone: 'neutral' },
    no_entry: { label: 'No entry signal', icon: '⏸️', tone: 'neutral' },
  }
  return knownStatuses[normalized] ?? {
    label: status.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()),
    icon: 'ℹ️',
    tone: 'neutral',
  }
}

function getSignalPresentation(signal: string | undefined): { label: string; icon: string; tone: string } {
  switch (signal?.toLowerCase()) {
    case 'long':
      return { label: 'BUY / Long', icon: '🟢', tone: 'success' }
    case 'short':
      return { label: 'SELL / Short', icon: '🔴', tone: 'error' }
    case 'flat':
      return { label: 'FLAT', icon: '⚪', tone: 'neutral' }
    default:
      return { label: signal ?? 'No signal', icon: '⚪', tone: 'neutral' }
  }
}

export function DashboardPage() {
  useForexSocket()
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [closeTarget, setCloseTarget] = useState<BrokerTrade | null>(null)
  const [isClosing, setIsClosing] = useState(false)
  const [closeError, setCloseError] = useState<string | null>(null)
  const [modifyPositionTarget, setModifyPositionTarget] = useState<BrokerTrade | null>(null)
  const [modifyPendingTarget, setModifyPendingTarget] = useState<BrokerPendingOrder | null>(null)
  const [modifyStopLoss, setModifyStopLoss] = useState('')
  const [modifyTakeProfit, setModifyTakeProfit] = useState('')
  const [modifyTrailingStopPips, setModifyTrailingStopPips] = useState('')
  const [modifyPendingPrice, setModifyPendingPrice] = useState('')
  const [modifyPendingUnits, setModifyPendingUnits] = useState('')
  const [isModifying, setIsModifying] = useState(false)
  const [modifyError, setModifyError] = useState<string | null>(null)
  const [positionView, setPositionView] = useState<'open' | 'closed'>('open')
  const selectedInstruments = useUiStore((state) => state.selectedInstruments)
  const [instrument, setInstrument] = useState(selectedInstruments[0] ?? 'EUR/USD')
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [units, setUnits] = useState(1200)
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market')
  const [limitPrice, setLimitPrice] = useState('')
  const [riskPercent, setRiskPercent] = useState(0.75)
  const [protectionMode, setProtectionMode] = useState<'pips' | 'percent' | 'price'>('pips')
  const [stopLossDistance, setStopLossDistance] = useState(15)
  const [takeProfitDistance, setTakeProfitDistance] = useState(25)
  const [orderStatus, setOrderStatus] = useState<{ status: string; orderId?: string; transactionId?: string; fillPrice?: string | null; environment?: string; reason?: string | null; cancelReason?: string | null } | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const accountQuery = useQuery({ queryKey: ['account'], queryFn: getAccountSummary })
  const marketQuery = useQuery({ queryKey: ['market'], queryFn: getMarketSummary })
  const marketStatusQuery = useQuery({ queryKey: ['market-status'], queryFn: getMarketStatus, refetchInterval: 30000, retry: 1 })
  const quoteQuery = useQuery({ queryKey: ['market-quote', instrument], queryFn: () => getMarketQuote(instrument), refetchInterval: 2500, retry: false })
  const modifyPositionQuoteQuery = useQuery({
    queryKey: ['manual-modify-quote', modifyPositionTarget?.symbol],
    queryFn: () => getMarketQuote(modifyPositionTarget!.symbol),
    enabled: Boolean(modifyPositionTarget),
    retry: false,
  })
  const positionsQuery = useQuery({ queryKey: ['broker-positions'], queryFn: getBrokerPositions, refetchInterval: 3000 })
  const pendingOrdersQuery = useQuery({ queryKey: ['broker-pending-orders'], queryFn: getBrokerPendingOrders, refetchInterval: 3000, retry: false })
  const autoExecutionQuery = useQuery({
    queryKey: ['strategy-auto-execution'],
    queryFn: getAutoExecutionStatus,
    refetchInterval: 5000,
    retry: false,
  })
  const riskQuery = useQuery({ queryKey: ['risk'], queryFn: getRiskSummary })
  const setupQuery = useQuery({ queryKey: ['setup-status'], queryFn: getSetupStatus })
  const [chartTimeframeOverride, setChartTimeframeOverride] = useState<string | null>(null)
  const chartPairKey = instrument.replace(/\//g, '_').toUpperCase()
  const chartTimeframe = chartTimeframeOverride
    ?? chartTimeframeFromStrategy(setupQuery.data?.pairTimeframes[chartPairKey], 'H1')
  const [chartCountMultiplier, setChartCountMultiplier] = useState(1)
  const chartQuery = useQuery({
    queryKey: ['dashboard-chart', instrument, chartTimeframe, chartCountMultiplier],
    queryFn: () => getOrdersChart(instrument, chartTimeframe, chartCandleCount(chartCountMultiplier)),
    refetchInterval: 60000,
    enabled: !setupQuery.isLoading,
  })
  const liveAccount = useUiStore((state) => state.accountFeed)
  const liveMarket = useUiStore((state) => state.marketFeed)
  const connectionState = useUiStore((state) => state.connectionState)
  const userRole = useUiStore((state) => state.userRole)
  const setOrdersFeed = useUiStore((state) => state.setOrdersFeed)
  const autoExecutionMutation = useMutation({
    mutationFn: (enabled: boolean) => setAutoExecution(enabled),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['strategy-auto-execution'] })
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ['strategy-auto-execution'] })
    },
  })
  const autoExecution = autoExecutionQuery.data
  const pairStatuses = autoExecution?.pairStatuses ?? []
  const runningPairCount = pairStatuses.filter((pair) => pair.status === 'running').length
  const stoppedPairCount = pairStatuses.length - runningPairCount
  const autoExecutionMutationError = autoExecutionMutation.error instanceof Error
    ? autoExecutionMutation.error.message
    : 'Automatic execution update rejected'

  const toggleAutoExecution = () => {
    const enable = !autoExecution?.enabled
    if (
      enable &&
      autoExecution?.environment === 'live' &&
      !window.confirm(
        'Enable automated execution for the confirmed OANDA Live account? Approved strategy signals can submit real market orders.',
      )
    ) {
      return
    }
    autoExecutionMutation.mutate(enable)
  }

  const account = liveAccount ?? accountQuery.data
  const market = liveMarket ?? marketQuery.data
  const marketStatus = marketStatusQuery.data
  const riskSummary = riskQuery.data
  const brokerQuote = quoteQuery.data
  const displayQuote = brokerQuote ?? market?.instruments.find((item) => item.pair === instrument)

  const instrumentOptions = selectedInstruments
  const activeInstrument = instrumentOptions.includes(instrument) ? instrument : instrumentOptions[0] ?? 'EUR/USD'

  useEffect(() => {
    if (instrument !== activeInstrument) {
      setInstrument(activeInstrument)
    }
  }, [activeInstrument, instrument])

  const recommendedUnits = useMemo(() => {
    if (!Number.isFinite(units) || units <= 0) return 0
    return Math.max(100, Math.round(units))
  }, [units])

  const entryPrice = brokerQuote ? Number(side === 'BUY' ? brokerQuote.ask : brokerQuote.bid) : Number.NaN
  const pricePrecision = brokerQuote?.displayPrecision ?? (instrument.endsWith('/JPY') ? 3 : 5)
  const activeEntryPrice = orderType === 'limit' ? Number(limitPrice) : entryPrice
  const pipSize = Number(brokerQuote?.pipSize ?? (instrument.endsWith('/JPY') ? 0.01 : 0.0001))
  const modifyPipSize = Number(modifyPositionQuoteQuery.data?.pipSize)
  const modifyPricePrecision = modifyPositionQuoteQuery.data?.displayPrecision ?? pricePrecision
  const modifyProtectionValidation = modifyPositionTarget
    ? validateTrailingStopInput(
        modifyStopLoss,
        modifyTrailingStopPips,
        modifyPipSize,
        modifyPricePrecision,
      )
    : null
  const modifyTrailingStopLossDistance = convertTrailingStopPipsToDistance(
    modifyTrailingStopPips,
    modifyPipSize,
    modifyPricePrecision,
  )
  const protectionPrice = (distance: number, kind: 'stop' | 'target') => {
    if (protectionMode === 'price') {
      return Number.isFinite(distance) && distance > 0 ? distance.toFixed(pricePrecision) : ''
    }
    if (!Number.isFinite(activeEntryPrice) || activeEntryPrice <= 0 || !Number.isFinite(distance) || distance <= 0) return ''
    const movesWithPosition = (kind === 'target') === (side === 'BUY')
    const offset = protectionMode === 'pips'
      ? distance * pipSize
      : activeEntryPrice * distance / 100
    return (activeEntryPrice + (movesWithPosition ? offset : -offset)).toFixed(pricePrecision)
  }
  const stopLossPrice = protectionPrice(stopLossDistance, 'stop')
  const takeProfitPrice = protectionPrice(takeProfitDistance, 'target')
  const orderUnitsAvailable = Number(brokerQuote?.unitsAvailable?.default?.[side === 'BUY' ? 'long' : 'short'])
  const quoteToAccountRate = Number(brokerQuote?.quoteToAccountRate)
  const accountCurrency = brokerQuote?.accountCurrency ?? brokerQuote?.quoteCurrency ?? '—'
  const conversionAvailable = Number.isFinite(quoteToAccountRate) && quoteToAccountRate > 0
  const tradeValue = Number.isFinite(activeEntryPrice)
    ? units * activeEntryPrice * (conversionAvailable ? quoteToAccountRate : 1)
    : Number.NaN
  const marginRate = Number(brokerQuote?.marginRate)
  const estimatedMargin = conversionAvailable && Number.isFinite(tradeValue) && Number.isFinite(marginRate)
    ? tradeValue * marginRate
    : Number.NaN
  const estimatedTakeProfit = Number.isFinite(activeEntryPrice) && takeProfitPrice && conversionAvailable
    ? Math.abs(Number(takeProfitPrice) - activeEntryPrice) * units * quoteToAccountRate
    : Number.NaN
  const pipValue = Number.isFinite(pipSize) && conversionAvailable
    ? pipSize * units * quoteToAccountRate
    : Number.NaN
  const tradeValueCurrency = conversionAvailable
    ? accountCurrency
    : brokerQuote?.quoteCurrency ?? '—'
  const minimumTradeSize = Number(brokerQuote?.minimumTradeSize ?? '1')
  const validUnits = Number.isInteger(units) && units >= minimumTradeSize
  const validLimitPrice = orderType !== 'limit' || (Number.isFinite(activeEntryPrice) && activeEntryPrice > 0)
  const validProtectionPrices = Boolean(stopLossPrice && takeProfitPrice)
    && (side === 'BUY'
      ? Number(stopLossPrice) < activeEntryPrice && Number(takeProfitPrice) > activeEntryPrice
      : Number(stopLossPrice) > activeEntryPrice && Number(takeProfitPrice) < activeEntryPrice)
  const orderValidationMessage = brokerQuote && !validUnits
    ? `Units must be a whole number of at least ${minimumTradeSize}.`
    : brokerQuote && Number.isFinite(orderUnitsAvailable) && units > orderUnitsAvailable
      ? `Units exceed the broker's live available amount (${orderUnitsAvailable.toLocaleString()}).`
      : brokerQuote && !validLimitPrice
        ? 'Enter a valid limit price.'
        : brokerQuote && !validProtectionPrices
      ? 'Stop loss and take profit must be valid prices on opposite sides of the entry.'
      : null
  const canSubmitOrder = userRole !== 'viewer' && !isSubmitting && brokerQuote?.tradeable === true && Number.isFinite(activeEntryPrice) && activeEntryPrice > 0 && validUnits && validLimitPrice && validProtectionPrices

  const handleOrderSubmit = async () => {
    if (!canSubmitOrder) {
      return
    }

    setIsSubmitting(true)
    try {
      const order = {
        symbol: instrument,
        side,
        volume: units,
        units,
        stopLoss: stopLossPrice,
        takeProfit: takeProfitPrice,
        riskPercent,
        clientOrderId: createManualClientOrderId(),
      }
      const result = orderType === 'limit'
        ? await submitLimitOrder({ ...order, price: limitPrice || activeEntryPrice.toFixed(pricePrecision) })
        : await submitMarketOrder(order)
      const fillPrice = 'fillPrice' in result ? result.fillPrice : null
      const reason = 'reason' in result ? result.reason ?? result.cancelReason ?? null : null
      const nextOrder = {
        id: result.orderId ?? `ui-order-${Date.now()}`,
        symbol: result.symbol,
        side: result.side === 'SELL' ? 'SELL' : 'BUY',
        volume: result.volume ?? String(units),
        status: result.status === 'filled' ? 'Filled' : result.status === 'cancelled' ? 'Cancelled' : ['queued', 'accepted', 'pending'].includes(result.status) ? 'Pending' : 'Rejected',
        createdAt: new Date().toISOString(),
        risk: `${riskPercent}%`,
      } as const
      setOrderStatus({
        status: result.status,
        orderId: result.orderId,
        transactionId: result.transactionId,
        fillPrice,
        environment: result.environment,
        reason,
        cancelReason: reason,
      })
      const feed = useUiStore.getState().ordersFeed ?? []
      setOrdersFeed([nextOrder, ...feed].slice(0, 10))
      await queryClient.invalidateQueries({ queryKey: ['broker-positions'] })
      await queryClient.invalidateQueries({ queryKey: ['broker-pending-orders'] })
      await queryClient.invalidateQueries({ queryKey: ['orders'] })
      setConfirmOpen(false)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Order submission rejected by backend'
      setOrderStatus({
        status: 'rejected',
        orderId: undefined,
        transactionId: undefined,
        fillPrice: null,
        environment: undefined,
        reason: message,
        cancelReason: message,
      })
      console.error(error)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handlePositionClose = async () => {
    if (!closeTarget || userRole === 'viewer') return
    setIsClosing(true)
    setCloseError(null)
    try {
      await closeManualPosition(closeTarget.id)
      await queryClient.invalidateQueries({ queryKey: ['broker-positions'] })
      setCloseTarget(null)
    } catch (error) {
      setCloseError(error instanceof Error ? error.message : 'Position close rejected by broker')
    } finally {
      setIsClosing(false)
    }
  }

  const handleModificationSubmit = async () => {
    if (userRole === 'viewer') return
    if (modifyProtectionValidation) {
      setModifyError(modifyProtectionValidation)
      return
    }
    setIsModifying(true)
    setModifyError(null)
    try {
      if (modifyPositionTarget) {
        await modifyManualPosition(
          modifyPositionTarget.id,
          {
            stopLoss: modifyStopLoss,
            takeProfit: modifyTakeProfit,
            trailingStopLossDistance: modifyTrailingStopLossDistance,
          },
        )
        await queryClient.invalidateQueries({ queryKey: ['broker-positions'] })
        setModifyPositionTarget(null)
      } else if (modifyPendingTarget) {
        await modifyPendingOrder(
          modifyPendingTarget.id,
          { price: modifyPendingPrice, units: modifyPendingUnits },
        )
        await queryClient.invalidateQueries({ queryKey: ['broker-pending-orders'] })
        setModifyPendingTarget(null)
      }
    } catch (error) {
      setModifyError(error instanceof Error ? error.message : 'Broker modification rejected')
    } finally {
      setIsModifying(false)
    }
  }

  const parseAmountValue = (value: string | undefined) => {
    if (!value) return 0
    const cleaned = value.replace(/,/g, '').replace(/[$,%\s]/g, '')
    const parsed = Number.parseFloat(cleaned)
    if (!Number.isFinite(parsed)) return 0
    if (/m/i.test(cleaned)) return parsed * 1_000_000
    if (/k/i.test(cleaned)) return parsed * 1_000
    return parsed
  }

  const totalExposureValue = (riskSummary?.exposureByPair ?? []).reduce((sum, item) => sum + parseAmountValue(item.value), 0)
  const marginBreakdown = (riskSummary?.exposureByPair ?? []).map((item, index) => {
    const colors = ['#22d3ee', '#a78bfa', '#34d399', '#fbbf24', '#fb7185', '#60a5fa']
    const portion = totalExposureValue > 0 ? (parseAmountValue(item.value) / totalExposureValue) * 100 : 0
    return { pair: item.pair, value: item.value, share: Math.round(portion), portion, color: colors[index % colors.length] }
  })

  const totalExposure = totalExposureValue.toLocaleString(undefined, { maximumFractionDigits: 0 })
  const usedPercent = account?.marginUsed ? Number.parseFloat(account.marginUsed.replace('%', '')) || 0 : null
  const usedMargin = account?.marginUsed ?? '—'
  const donutStyle = {
    background: totalExposureValue > 0
      ? `conic-gradient(${marginBreakdown.map((item, index) => {
          const start = marginBreakdown.slice(0, index).reduce((sum, current) => sum + current.portion, 0)
          return `${item.color} ${start}% ${start + item.portion}%`
        }).join(', ')})`
      : 'conic-gradient(#334155 0 100%)',
  }

  const riskHeatmap = (riskSummary?.exposureByPair ?? []).map((item) => {
    const share = totalExposureValue > 0 ? Math.round((parseAmountValue(item.value) / totalExposureValue) * 100) : 0
    const level = share >= 60 ? 'high' : share >= 30 ? 'mid' : 'safe'
    return { pair: item.pair, value: item.value, share, level }
  })

  const pairLeaderboard = (riskSummary?.exposureByPair ?? []).map((item, index) => ({
    pair: item.pair,
    share: marginBreakdown[index]?.share ?? 0,
    value: item.value,
  }))

  const visiblePositions = positionsQuery.data?.[positionView] ?? []
  const isLiveFeedConnected = connectionState === 'online' && Boolean(liveMarket || liveAccount)
  const marketClock = marketStatusQuery.dataUpdatedAt
  const marketCountdown = formatMarketCountdown(marketStatus?.nextTransitionAt, marketClock)
  const marketTransitionMs = marketStatus?.nextTransitionAt ? Date.parse(marketStatus.nextTransitionAt) - marketClock : null
  const isClosingSoon = marketStatus?.state === 'open'
    && marketStatus.nextTransition === 'close'
    && marketTransitionMs !== null
    && marketTransitionMs >= 0
    && marketTransitionMs <= 60 * 60 * 1000
  const showPortfolioCockpit = setupQuery.data?.showPortfolioCockpit === true
  const strategyStatus = autoExecutionQuery.isError
    ? { label: '⚠️ Status unavailable', tone: 'error' }
    : autoExecutionQuery.isLoading && !autoExecution
      ? { label: '🔄 Checking status…', tone: 'pending' }
      : autoExecution?.enabled
        ? autoExecution.workerRunning
          ? { label: `🟢 Worker active · ${autoExecution.environment.toUpperCase()}`, tone: 'success' }
          : { label: `🚨 Worker stopped · ${autoExecution.environment.toUpperCase()}`, tone: 'error' }
        : { label: '⏸️ Automation off', tone: 'neutral' }

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h2>Trading overview</h2>
        </div>

        <div className="topbar-actions">
          <div className={`status-pill ${connectionState === 'offline' ? 'offline' : isLiveFeedConnected ? 'online' : 'neutral'}`}>
            {connectionState === 'offline' ? '⚠️ Feed offline' : isLiveFeedConnected ? '🟢 Broker feed live' : '🔄 Connecting to broker'}
          </div>
          <div
            className={`status-pill market-status-pill ${
              marketStatus?.state === 'open'
                ? isClosingSoon ? 'market-closing' : 'market-open'
                : marketStatus?.state === 'closed_weekly'
                  ? 'market-closed'
                  : marketStatus?.state === 'broker_closed'
                    ? 'market-holiday'
                    : 'neutral'
            }`}
            title={marketStatusQuery.isError
              ? marketStatusQuery.error instanceof Error ? marketStatusQuery.error.message : 'Market status unavailable'
              : `${marketStatus?.schedule ?? 'FX market session'} · ${marketStatus?.timezone ?? 'New York time'} · ${marketStatus?.source ?? ''}`}
            aria-live="polite"
          >
            {marketStatusQuery.isError
              ? '⚠️ Market status unavailable'
              : marketStatus?.state === 'open'
                ? isClosingSoon
                  ? `⚠️ FX closing soon · ${marketCountdown ?? 'under 1m'}`
                  : `🟢 FX market open · closes in ${marketCountdown ?? '—'}`
                : marketStatus?.state === 'closed_weekly'
                  ? `🌙 FX market closed · opens in ${marketCountdown ?? '—'}`
                  : marketStatus?.state === 'broker_closed'
                    ? '🏖️ Broker closed · holiday or halt possible'
                    : marketStatus?.scheduledOpen
                      ? `🟡 Broker status unknown · scheduled close in ${marketCountdown ?? '—'}`
                      : `🌙 FX market closed · opens in ${marketCountdown ?? '—'}`}
          </div>
        </div>
      </header>

      <section className={`dashboard-parent-grid ${showPortfolioCockpit ? '' : 'dashboard-parent-grid--portfolio-hidden'}`}>
        <div className="panel dashboard-parent dashboard-parent--market">
          <div className="dashboard-parent-heading">
            <div><p className="eyebrow">Market & execution</p><h3>📈 Trading desk</h3></div>
            <span className="dashboard-parent-count">{positionsQuery.data?.open.length ?? '—'} open trades</span>
          </div>
          <section className="content-grid">
        <div className="main-column">
          <div className="panel chart-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Live chart</p>
                <h3>📊 {instrument} price action</h3>
              </div>
              <ChartDataControls timeframe={chartTimeframe} countMultiplier={chartCountMultiplier} onTimeframeChange={setChartTimeframeOverride} onCountMultiplierChange={setChartCountMultiplier} />
            </div>

            {chartQuery.isLoading && <p className="chart-empty-state">Loading real chart data…</p>}
            {chartQuery.isError && <p className="chart-empty-state">Real chart data unavailable; retrying from backend.</p>}
            {chartQuery.data && (
              <div className="real-chart-panel">
                <ForexChart data={chartQuery.data} />
              </div>
            )}
          </div>

          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Live feed</p>
                <h3>🌍 Market watchlist</h3>
              </div>
              <span className="muted-cell">{market?.instruments.length ?? 0} instruments</span>
            </div>

            <div className="watchlist">
              {marketQuery.isError && <p className="inline-error">Market data unavailable. Check the API connection.</p>}
              {market?.instruments.map((item) => (
                <div key={item.pair} className="watch-row">
                  <div className="pair-block">
                    <span className="pair-name">{item.pair}</span>
                    <span className={`pair-change ${item.change.trim().startsWith('-') ? 'negative' : 'positive'}`}>{item.change}</span>
                  </div>
                  <div className="price-block">
                    <span>Bid</span>
                    <strong>{item.bid}</strong>
                  </div>
                  <div className="price-block">
                    <span>Ask</span>
                    <strong>{item.ask}</strong>
                  </div>
                  <div className="price-block">
                    <span>Spread</span>
                    <strong>{item.spread}</strong>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Broker positions</p>
                <h3>💹 Trade monitor</h3>
              </div>
              <span className="positions-sync-status" aria-live="polite">
                {positionsQuery.dataUpdatedAt
                  ? `🟢 Live · Updated ${new Date(positionsQuery.dataUpdatedAt).toLocaleTimeString()}`
                  : '🔄 Connecting to broker…'}
              </span>
              <div className="segmented" role="tablist" aria-label="Trade status">
                <button type="button" role="tab" aria-selected={positionView === 'open'} className={positionView === 'open' ? 'segment active' : 'segment'} onClick={() => setPositionView('open')}>Open</button>
                <button type="button" role="tab" aria-selected={positionView === 'closed'} className={positionView === 'closed' ? 'segment active' : 'segment'} onClick={() => setPositionView('closed')}>Closed</button>
              </div>
            </div>

            {positionsQuery.isError && <p className="inline-error">Broker trade data unavailable. Check the Practice account connection.</p>}
            <div className="position-table-scroll">
              <table className="positions-table broker-positions-table">
                <thead>
                  <tr>
                    <th>Source</th><th>Symbol</th><th>Side</th><th>Units</th><th>Entry</th>
                    <th>{positionView === 'open' ? 'Current' : 'Exit'}</th><th>Protection</th><th>TP</th><th>P/L {positionsQuery.data?.accountCurrency ? `(${positionsQuery.data.accountCurrency})` : ''}</th><th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {visiblePositions.map((position) => (
                    <tr key={position.id} className={position.manual ? 'manual-position-row' : undefined}>
                      <td><span className={position.manual ? 'manual-trade-tag' : 'strategy-trade-tag'}>{position.manual ? 'Manual' : position.source === 'auto' ? 'Auto strategy' : 'Strategy'}</span></td>
                      <td>{position.symbol}</td>
                      <td>
                        <span className={`position-side-badge ${position.side === 'BUY' ? 'long' : 'short'}`}>
                          {position.side === 'BUY' ? '🟢 ▲ LONG' : '🔴 ▼ SHORT'}
                        </span>
                      </td>
                      <td>{Number(position.units).toLocaleString()}</td>
                      <td>{position.entryPrice || '—'}</td>
                      <td>{positionView === 'open' ? position.currentPrice ?? '—' : position.exitPrice ?? '—'}</td>
                      <td>
                        <div className="position-protection">
                          <span className={`position-stop-badge ${position.stopLossType === 'trailing' ? 'trailing' : position.stopLossType === 'none' || (!position.stopLossType && !position.stopLoss) ? 'unprotected' : 'fixed'}`}>
                            {position.stopLossType === 'trailing'
                              ? '🛡️ Trailing SL · Active'
                              : position.stopLossType === 'none' || (!position.stopLossType && !position.stopLoss)
                                ? '⚠️ No stop'
                                : '🔒 Fixed SL'}
                          </span>
                          <strong>{position.stopLoss ?? '—'}</strong>
                          {position.stopLossType === 'trailing' && position.trailingStopDistance && (
                            <small>Gap {position.trailingStopDistance}</small>
                          )}
                        </div>
                      </td>
                      <td>{position.takeProfit ?? '—'}</td>
                      <td className={Number(position.pnl) < 0 ? 'negative' : 'positive'}>{Number(position.pnl) > 0 ? '+' : ''}{position.pnl}</td>
                      <td>{positionView === 'open' && (position.manual || position.source === 'auto') && userRole !== 'viewer' ? <div className="trade-actions">{position.manual && <button className="modify-position-button" type="button" onClick={() => { setModifyError(null); setModifyStopLoss(position.stopLoss ?? ''); setModifyTakeProfit(position.takeProfit ?? ''); setModifyTrailingStopPips(''); setModifyPositionTarget(position) }}>Modify</button>}<button className="close-position-button" type="button" onClick={() => { setCloseError(null); setCloseTarget(position) }}>Close</button></div> : positionView === 'closed' ? <span className="muted-cell">Closed</span> : '—'}</td>
                    </tr>
                  ))}
                  {!positionsQuery.isLoading && visiblePositions.length === 0 && <tr><td className="empty-cell" colSpan={10}>{positionsQuery.isError ? 'Broker positions could not be loaded.' : `No ${positionView} broker trades.`}</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div><p className="eyebrow">Broker orders</p><h3>🧾 Pending orders</h3></div>
              <span className="muted-cell">Live OANDA data</span>
            </div>
            {pendingOrdersQuery.isError && <p className="inline-error">Broker pending orders could not be loaded.</p>}
            <div className="position-table-scroll">
              <table className="positions-table">
                <thead><tr><th>ID</th><th>Symbol</th><th>Side</th><th>Units</th><th>Price</th><th>Action</th></tr></thead>
                <tbody>
                  {(pendingOrdersQuery.data ?? []).map((order) => (
                    <tr key={order.id}>
                      <td>{order.id}</td><td>{order.symbol}</td><td className={order.side === 'BUY' ? 'long' : 'short'}>{order.side}</td>
                      <td>{Number(order.volume).toLocaleString()}</td><td>{order.price}</td>
                      <td>{order.manual && userRole !== 'viewer' ? <button className="modify-position-button" type="button" onClick={() => { setModifyError(null); setModifyPendingPrice(order.price); setModifyPendingUnits(order.volume); setModifyPendingTarget(order) }}>Modify</button> : <span className="muted-cell">Broker-managed</span>}</td>
                    </tr>
                  ))}
                  {!pendingOrdersQuery.isLoading && !pendingOrdersQuery.isError && (pendingOrdersQuery.data?.length ?? 0) === 0 && <tr><td className="empty-cell" colSpan={6}>No broker pending orders.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Exposure</p>
                <h3>🔥 Exposure heatmap</h3>
              </div>
            </div>

            <div className="heatmap-grid">
              {riskHeatmap.map((cell) => (
                <div key={cell.pair} className={`heatmap-card ${cell.level}`}>
                  <span>{cell.pair} · {cell.share}% of exposure</span>
                  <strong>{cell.value}</strong>
                </div>
              ))}
              {!riskQuery.isLoading && riskHeatmap.length === 0 && <p className="empty-cell">No exposure data available.</p>}
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Alerts</p>
                <h3>🚨 Risk & events</h3>
              </div>
            </div>

            <div className="alert-list">
              {market?.alerts.map((alert, index) => (
                <div key={`${alert.title}-${index}`} className="alert-item">
                  <span className="alert-dot" />
                  <div>
                    <strong>{alert.title}</strong>
                    <p>{alert.detail}</p>
                  </div>
                </div>
              ))}
              {!marketQuery.isLoading && (market?.alerts.length ?? 0) === 0 && <p className="empty-cell">No active market alerts.</p>}
            </div>
          </div>
        </div>

        <div className="side-column">
          <div className="panel chart-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Exposure mix</p>
                <h3>🍩 Portfolio allocation</h3>
              </div>
              <span className="pill neutral">{usedPercent === null ? 'Margin —' : `${usedPercent}% margin`}</span>
            </div>

            <div className="margin-donut-shell">
              <div className="margin-donut" style={donutStyle} aria-label="Portfolio exposure by pair">
                <div className="margin-donut-center">
                  <span>Total exposure</span>
                  <strong>{riskSummary ? totalExposure : '—'}</strong>
                  <small>{usedMargin} margin used</small>
                </div>
              </div>
            </div>

            <div className="margin-breakdown" aria-label="Exposure breakdown by pair">
              {marginBreakdown.map((item) => (
                <div key={item.pair} className="margin-breakdown-row">
                  <div className="margin-meta">
                    <span className="margin-swatch" style={{ background: item.color }} />
                    <span>{item.pair}</span>
                  </div>
                  <div className="margin-values">
                    <strong>{item.value}</strong>
                    <small>{item.share}%</small>
                  </div>
                </div>
              ))}
              {!riskQuery.isLoading && marginBreakdown.length === 0 && <p className="empty-cell">Exposure distribution will appear when live risk data is available.</p>}
            </div>
          </div>

          <div className="panel account-snapshot-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Live account</p>
                <h3>💎 Account snapshot</h3>
              </div>
              {riskSummary && <span className={`pill ${riskSummary.killSwitch ? 'negative' : 'positive'}`}>{riskSummary.killSwitch ? 'Kill switch active' : 'Risk controls ready'}</span>}
            </div>
            <div className="account-snapshot-grid">
              <div className="snapshot-tile snapshot-tile--cyan"><span>Equity</span><strong>{account?.equity ?? '—'}</strong></div>
              <div className={`snapshot-tile ${parseAmountValue(account?.netPnl) < 0 ? 'snapshot-tile--pink' : 'snapshot-tile--green'}`}><span>Net P/L</span><strong>{account?.netPnl ?? '—'}</strong></div>
              <div className="snapshot-tile snapshot-tile--violet"><span>Open exposure</span><strong>{account?.exposure ?? '—'}</strong></div>
              <div className="snapshot-tile snapshot-tile--amber"><span>Open trades</span><strong>{positionsQuery.data?.open.length ?? '—'}</strong></div>
              <div className="snapshot-tile snapshot-tile--pink"><span>Daily risk</span><strong>{riskSummary?.dailyLoss ?? account?.dailyRisk ?? '—'}</strong></div>
              <div className="snapshot-tile snapshot-tile--blue"><span>Max drawdown</span><strong>{account?.drawdown ?? '—'}</strong></div>
              <div className="snapshot-tile snapshot-tile--cyan"><span>Margin level</span><strong>{riskSummary?.marginLevel ?? '—'}</strong></div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Leaderboard</p>
                <h3>🏆 Pairs by exposure</h3>
              </div>
            </div>

            <div className="leaderboard-list">
              {pairLeaderboard.map((item, index) => (
                <div key={item.pair} className="leaderboard-row">
                  <span className="leaderboard-rank">#{index + 1}</span>
                  <div className="leaderboard-meta">
                    <strong>{item.pair}</strong>
                    <span>{item.value} · {item.share}%</span>
                  </div>
                  <span className="leaderboard-pnl">{item.share}%</span>
                </div>
              ))}
              {!riskQuery.isLoading && pairLeaderboard.length === 0 && <p className="empty-cell">No pair exposure data available.</p>}
            </div>
          </div>

          <div className="panel strategy-engine-panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Strategy engine</p>
                <h3>🤖 Signals & automation</h3>
              </div>
              <span className={`strategy-status-badge ${strategyStatus.tone}`} aria-live="polite">
                {strategyStatus.label}
              </span>
            </div>

            <div className="strategy-auto-controls">
              <div className="strategy-control-copy">
                <strong>🧠 Automated execution</strong>
                <p>
                  Only approved strategies can place orders. Risk limits, stop loss, allowed side,
                  broker tradeability, and available depth are checked before entry.
                </p>
              </div>
              {autoExecutionQuery.isError && (
                <small className="inline-error strategy-error" role="alert">
                  {autoExecutionQuery.error instanceof Error
                    ? autoExecutionQuery.error.message
                    : 'Automatic execution status unavailable'}
                </small>
              )}
              {autoExecutionMutation.isError && (
                <small className="inline-error strategy-error" role="alert">
                  {autoExecutionMutationError.startsWith('Automatic execution was not started.')
                    ? 'Automation was not started. Review the stopped pairs and their settings below.'
                    : autoExecutionMutationError}
                </small>
              )}
            </div>

            <div className="strategy-live-summary" aria-label="Live automation status">
              <div className="strategy-live-stat">
                <span>Automation</span>
                <strong className={autoExecution?.enabled ? 'success-text' : 'neutral-text'}>
                  {autoExecutionQuery.isError ? 'Unavailable' : autoExecution?.enabled ? 'Enabled' : autoExecutionQuery.isLoading ? 'Checking…' : 'Off'}
                </strong>
              </div>
              <div className="strategy-live-stat">
                <span>Worker</span>
                <strong className={autoExecution?.workerRunning ? 'success-text' : autoExecution?.enabled ? 'error-text' : 'neutral-text'}>
                  {autoExecutionQuery.isError ? 'Unknown' : autoExecution?.workerRunning ? 'Running' : autoExecution?.enabled ? 'Stopped' : 'Idle'}
                </strong>
              </div>
              <div className="strategy-live-stat">
                <span>Environment</span>
                <strong>{autoExecutionQuery.isError ? 'Unknown' : autoExecution?.environment?.toUpperCase() ?? '—'}</strong>
              </div>
              <div className="strategy-live-stat">
                <span>Last cycle</span>
                <strong>{autoExecution?.lastCycleAt ? new Date(autoExecution.lastCycleAt).toLocaleString() : 'No cycle yet'}</strong>
              </div>
            </div>

            {autoExecution?.lastError
              && !autoExecution.lastError.startsWith('Automatic execution was not started.') && (
              <div className="strategy-worker-notice" role="status">
                <strong>⚠️ Automation notice</strong>
                <span>{autoExecution.lastError}</span>
              </div>
            )}

            <section className="strategy-pair-status" aria-labelledby="strategy-pair-status-title">
              <div className="strategy-section-heading">
                <div>
                  <strong id="strategy-pair-status-title">🧭 Pair execution status</strong>
                  <small>Pairs with complete settings run independently; blocked pairs remain stopped.</small>
                </div>
                <span className="strategy-pair-counts">
                  <span className="strategy-pair-count-running">🟢 {runningPairCount} running</span>
                  <span className="strategy-pair-count-stopped">⏸️ {stoppedPairCount} stopped</span>
                </span>
              </div>
              <div className="strategy-pair-status-list" aria-live="polite">
                {pairStatuses.map((pair) => (
                  <article
                    className={`strategy-pair-row ${pair.status}`}
                    key={pair.pair}
                  >
                    <div className="strategy-pair-heading">
                      <strong>{pair.pair}</strong>
                      <span className={`strategy-pair-badge ${pair.status}`}>
                        {pair.status === 'running' ? '🟢 Running' : '⏸️ Stopped'}
                      </span>
                    </div>
                    {pair.reasons.length > 0 && (
                      <ul className="strategy-pair-reasons">
                        {pair.reasons.map((reason) => {
                          const normalizedReason = reason.toLowerCase()
                          const category = normalizedReason.includes('strategy')
                            ? 'strategy'
                            : normalizedReason.includes('risk')
                              || normalizedReason.includes('stop loss')
                              || normalizedReason.includes('exposure')
                              || normalizedReason.includes('entry side')
                                ? 'risk'
                                : 'setup'
                          const icon = category === 'strategy' ? '🧠' : category === 'risk' ? '🛡️' : '⚙️'
                          return (
                            <li className={`strategy-pair-reason ${category}`} key={reason}>
                              <span aria-hidden="true">{icon}</span>
                              <span>{reason}</span>
                            </li>
                          )
                        })}
                      </ul>
                    )}
                  </article>
                ))}
                {!autoExecutionQuery.isLoading && !autoExecutionQuery.isError && pairStatuses.length === 0 && (
                  <p className="strategy-empty-state">No configured pairs are available for status reporting.</p>
                )}
                {autoExecutionQuery.isLoading && (
                  <p className="strategy-empty-state">Loading pair readiness…</p>
                )}
              </div>
            </section>

            <section className="strategy-results" aria-labelledby="strategy-results-title">
              <div className="strategy-section-heading">
                <div>
                  <strong id="strategy-results-title">📡 Latest execution results</strong>
                  <small>Automatically refreshes every 5 seconds</small>
                </div>
                <div className="strategy-refresh-state" aria-live="polite" aria-atomic="true">
                  <span className={`strategy-refresh-indicator ${autoExecutionQuery.isFetching ? 'is-refreshing' : ''}`} aria-hidden="true" />
                  <span>{autoExecutionQuery.isFetching ? 'Refreshing' : 'Live status'}</span>
                </div>
              </div>
              <div className="strategy-results-list">
                {autoExecutionQuery.isLoading && <p className="strategy-empty-state">Connecting to the strategy worker…</p>}
                {autoExecution?.results.slice(0, 5).map((result) => {
                  const status = formatExecutionResultStatus(result.status)
                  const signal = getSignalPresentation(result.signal)
                  return (
                    <article className="strategy-result-card" key={`${result.pair}-${result.candleTime}-${result.status}`}>
                      <div className="strategy-result-heading">
                        <div>
                          <strong>{result.pair}</strong>
                          <small>{result.timeframe}{result.candleTime ? ` · ${new Date(result.candleTime).toLocaleString()}` : ''}</small>
                        </div>
                        <span className={`strategy-result-status ${status.tone}`}>{status.icon} {status.label}</span>
                      </div>
                      <div className="strategy-result-details">
                        <span className={`strategy-signal ${signal.tone}`}>{signal.icon} {signal.label}</span>
                        {result.units !== undefined && <span>📦 {Number(result.units).toLocaleString()} units</span>}
                        {result.fillPrice && <span>💱 Fill {result.fillPrice}</span>}
                      </div>
                      {result.reason && <p>{result.reason}</p>}
                    </article>
                  )
                })}
                {!autoExecutionQuery.isLoading && !autoExecutionQuery.isError && (autoExecution?.results.length ?? 0) === 0 && (
                  <p className="strategy-empty-state">No strategy cycles have reported results yet.</p>
                )}
                {autoExecutionQuery.isError && (
                  <p className="strategy-empty-state" role="status">Live execution results are unavailable until the connection is restored.</p>
                )}
              </div>
            </section>

            <section className="strategy-overview" aria-labelledby="strategy-overview-title">
              <div className="strategy-section-heading">
                <div>
                  <strong id="strategy-overview-title">🗂️ Strategy overview</strong>
                  <small>Informational signals only — these cards do not trigger orders.</small>
                </div>
                <span>{market?.strategySignals.length ?? 0} strategies</span>
              </div>
              {marketQuery.isError && <p className="strategy-empty-state">Strategy overview is unavailable. Check the market data connection.</p>}
              <div className="strategy-stack">
                {market?.strategySignals.map((strategy) => (
                  <article key={strategy.name} className="strategy-card">
                  <div className="strategy-row">
                    <strong>🤖 {strategy.name}</strong>
                    <span className="strategy-mode-badge">⚙️ {strategy.mode}</span>
                  </div>
                  <div className="strategy-meta">
                    <span>📍 {strategy.status}</span>
                    <span>⭐ {strategy.quality}</span>
                  </div>
                    <p>{strategy.signal}</p>
                  </article>
                ))}
              </div>
              {!marketQuery.isLoading && !marketQuery.isError && (market?.strategySignals.length ?? 0) === 0 && (
                <p className="strategy-empty-state">No strategy overview is available for the current market.</p>
              )}
            </section>
            <div className="strategy-control-actions strategy-panel-actions">
              <button
                type="button"
                className={autoExecution?.enabled ? 'danger-action' : 'primary-action'}
                disabled={
                  userRole === 'viewer' ||
                  autoExecutionMutation.isPending ||
                  autoExecutionQuery.isLoading ||
                  autoExecutionQuery.isError
                }
                onClick={toggleAutoExecution}
              >
                {autoExecutionMutation.isPending
                  ? 'Updating…'
                  : autoExecution?.enabled
                    ? '⏹ Stop automation'
                    : '▶ Enable automation'}
              </button>
              <button
                type="button"
                className={`secondary-action strategy-refresh-button ${autoExecutionQuery.isFetching ? 'is-refreshing' : ''}`}
                aria-label={autoExecutionQuery.isFetching ? 'Refreshing strategy status' : 'Refresh strategy status'}
                onClick={() => void autoExecutionQuery.refetch()}
              >
                <span className="strategy-refresh-icon" aria-hidden="true">↻</span>
                <span>Refresh status</span>
              </button>
            </div>
          </div>
        </div>
      </section>

        </div>
        {showPortfolioCockpit && (
        <div className="panel dashboard-parent dashboard-parent--account">
          <div className="dashboard-parent-heading">
            <div><p className="eyebrow">Account & execution</p><h3>🧭 Portfolio cockpit</h3></div>
            <span className="dashboard-parent-count">{usedPercent === null ? 'Margin —' : `Margin ${usedPercent}%`}</span>
          </div>
      <section className="lower-grid">
        <div className="panel order-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Execution controls</p>
              <h3>⚡ Order ticket</h3>
            </div>
          </div>

          <div className="order-form">
            <div className="quote-strip">
              <div className="quote-strip-heading"><strong>{activeInstrument}</strong>              <span className={brokerQuote?.tradeable ? 'quote-live' : 'quote-delayed'}>{brokerQuote?.tradeable ? `${brokerQuote.environment.toUpperCase()} CONNECTED` : quoteQuery.isError ? 'QUOTE UNAVAILABLE' : 'WAITING FOR BROKER'}</span></div>
              <div className="quote-values">
                <div><span>Bid</span><strong>{displayQuote ? Number(displayQuote.bid).toFixed(pricePrecision) : '—'}</strong></div>
                <div><span>Ask</span><strong>{displayQuote ? Number(displayQuote.ask).toFixed(pricePrecision) : '—'}</strong></div>
                <div><span>Spread</span><strong>{displayQuote ? Number(displayQuote.spread).toFixed(pricePrecision) : '—'}</strong></div>
              </div>
              <small>{brokerQuote ? `Updated ${new Date(brokerQuote.time).toLocaleTimeString()} · ${brokerQuote.environment}` : quoteQuery.isError ? 'No broker quote. Orders are disabled.' : 'Connecting to broker quote…'}</small>
            </div>

            <div className="field-row">
              <label>
                <span>Instrument</span>
                <select value={activeInstrument} onChange={(event) => { setInstrument(event.target.value); setLimitPrice('') }}>
                  {instrumentOptions.map((pair) => (
                    <option key={pair} value={pair}>{pair}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>Side</span>
                <select value={side} onChange={(event) => setSide(event.target.value as 'BUY' | 'SELL')}>
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </label>
              <label>
                <span>Order type</span>
                <select value={orderType} onChange={(event) => setOrderType(event.target.value as 'market' | 'limit')}>
                  <option value="market">Market</option>
                  <option value="limit">Limit</option>
                </select>
              </label>
            </div>

            <div className="field-row">
              <label>
                <span>Units</span>
                <input type="number" min={minimumTradeSize} step="1" value={units} onChange={(event) => setUnits(Number(event.target.value) || 0)} />
              </label>
              <label>
                <span>Risk %</span>
                <input type="number" step="0.01" value={riskPercent} onChange={(event) => setRiskPercent(Number(event.target.value) || 0)} />
              </label>
              {orderType === 'limit' && (
                <label>
                  <span>Limit price</span>
                  <input type="number" min="0" step={10 ** -pricePrecision} value={limitPrice || (Number.isFinite(entryPrice) ? entryPrice.toFixed(pricePrecision) : '')} onChange={(event) => setLimitPrice(event.target.value)} />
                </label>
              )}
            </div>

            <div className="field-row">
              <label>
                <span>Protection input</span>
                <select value={protectionMode} onChange={(event) => setProtectionMode(event.target.value as 'pips' | 'percent' | 'price')}>
                  <option value="pips">Pips</option>
                  <option value="price">Price</option>
                  <option value="percent">Percent</option>
                </select>
                <small className="calculated-price">1 pip = {pipSize.toFixed(pricePrecision)} {instrument.split('/')[1] ?? ''}</small>
              </label>
              <label>
                <span>Stop loss {protectionMode === 'pips' ? '(pips)' : protectionMode === 'percent' ? '(%)' : '(price)'}</span>
                <input type="number" min="0" step={protectionMode === 'price' ? 10 ** -pricePrecision : protectionMode === 'pips' ? '1' : '0.01'} value={stopLossDistance} onChange={(event) => setStopLossDistance(Number(event.target.value) || 0)} />
                <small className="calculated-price">Broker stop: {stopLossPrice || '—'}</small>
              </label>
              <label>
                <span>Take profit {protectionMode === 'pips' ? '(pips)' : protectionMode === 'percent' ? '(%)' : '(price)'}</span>
                <input type="number" min="0" step={protectionMode === 'price' ? 10 ** -pricePrecision : protectionMode === 'pips' ? '1' : '0.01'} value={takeProfitDistance} onChange={(event) => setTakeProfitDistance(Number(event.target.value) || 0)} />
                <small className="calculated-price">Broker target: {takeProfitPrice || '—'}</small>
              </label>
            </div>

            <div className="dom-panel">
              <div className="dom-heading">
                <strong>Depth of Market</strong>
                <span>{brokerQuote?.bids?.length || brokerQuote?.asks?.length ? 'OANDA liquidity levels' : 'Top quote only'}</span>
              </div>
              <p className="dom-note">{brokerQuote?.bids?.length || brokerQuote?.asks?.length ? 'Broker-provided price/liquidity levels; not a centralized FX order book. Select a row to create a limit order at that price.' : 'No depth was returned by the broker. Suggested fallback: selectable top Bid/Ask only; volume is unavailable.'}</p>
              <div className="position-table-scroll">
                <table className="positions-table dom-table">
                  <thead><tr><th>Buy units</th><th>Price</th><th>Sell units</th></tr></thead>
                  <tbody>
                    {(brokerQuote?.asks?.length
                      ? [...brokerQuote.asks].sort((a, b) => Number(b.price) - Number(a.price)).slice(0, 5)
                      : brokerQuote ? [{ price: brokerQuote.ask, units: '—' }] : []
                    ).map((level, index) => (
                      <tr key={`ask-${index}`} className="dom-ask-row">
                        <td>{Number.isFinite(Number(level.units)) ? Number(level.units).toLocaleString() : level.units}</td>
                        <td><button type="button" onClick={() => { setSide('BUY'); setOrderType('limit'); setLimitPrice(level.price) }}>{Number(level.price).toFixed(pricePrecision)}</button></td>
                        <td>—</td>
                      </tr>
                    ))}
                    <tr className="dom-spread-row"><td colSpan={3}>Spread {displayQuote ? Number(displayQuote.spread).toFixed(pricePrecision) : '—'}</td></tr>
                    {(brokerQuote?.bids?.length
                      ? [...brokerQuote.bids].sort((a, b) => Number(b.price) - Number(a.price)).slice(0, 5)
                      : brokerQuote ? [{ price: brokerQuote.bid, units: '—' }] : []
                    ).map((level, index) => (
                      <tr key={`bid-${index}`} className="dom-bid-row">
                        <td>—</td>
                        <td><button type="button" onClick={() => { setSide('SELL'); setOrderType('limit'); setLimitPrice(level.price) }}>{Number(level.price).toFixed(pricePrecision)}</button></td>
                        <td>{Number.isFinite(Number(level.units)) ? Number(level.units).toLocaleString() : level.units}</td>
                      </tr>
                    ))}
                    {!brokerQuote && <tr><td colSpan={3} className="empty-cell">Waiting for live broker levels…</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="live-order-metrics">
              <div><span>Units available</span><strong>{Number.isFinite(orderUnitsAvailable) ? orderUnitsAvailable.toLocaleString() : '—'}</strong></div>
              <div><span>Value / pip</span><strong>{Number.isFinite(pipValue) ? `${pipValue.toFixed(2)} ${accountCurrency}` : '—'}</strong></div>
              <div><span>Take profit estimate</span><strong>{Number.isFinite(estimatedTakeProfit) ? `${estimatedTakeProfit.toFixed(2)} ${accountCurrency}` : '—'}</strong><small>At selected target</small></div>
              <div><span>Trade value</span><strong>{Number.isFinite(tradeValue) ? `${tradeValue.toFixed(2)} ${tradeValueCurrency}` : '—'}</strong></div>
              <div><span>Margin required (est.)</span><strong>{Number.isFinite(estimatedMargin) ? `${estimatedMargin.toFixed(2)} ${accountCurrency}` : '—'}</strong><small>{brokerQuote?.marginRate ? `Margin rate ${Number(brokerQuote.marginRate) * 100}%` : 'Broker margin rate unavailable'}</small></div>
              <div><span>Margin available</span><strong>{brokerQuote?.marginAvailable ? `${Number(brokerQuote.marginAvailable).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${accountCurrency}` : '—'}</strong><small>{brokerQuote?.conversionError ? 'Conversion quote unavailable' : 'Live broker account'}</small></div>
            </div>

            <div className="field-row compact-row">
              <div className="recommendation-box">
                <span>Suggested size</span>
                <strong>{recommendedUnits.toLocaleString()} units</strong>
              </div>
              <div className="status-box">
                <span>Order status</span>
                <strong>{orderStatus ? orderStatus.status : 'Idle'}</strong>
              </div>
            </div>

            {orderValidationMessage && <small className="calculated-price" role="alert">{orderValidationMessage}</small>}

            <div className="actions-row">
              <button
                className="primary-action"
                disabled={!canSubmitOrder}
                onClick={() => setConfirmOpen(true)}
                style={{ opacity: canSubmitOrder ? 1 : 0.5 }}
              >
                {isSubmitting ? 'Submitting...' : orderType === 'limit' ? 'Submit limit order' : 'Submit market order'}
              </button>
              <button className="secondary-action" type="button" onClick={() => setOrderStatus(null)}>Reset</button>
            </div>

            {orderStatus && (
              <div className="status-rail">
                <div><span>Environment</span><strong>{orderStatus.environment ?? 'practice'}</strong></div>
                <div><span>Order ID</span><strong>{orderStatus.orderId ?? '—'}</strong></div>
                <div><span>Transaction</span><strong>{orderStatus.transactionId ?? '—'}</strong></div>
                <div><span>Fill price</span><strong>{orderStatus.fillPrice ?? '—'}</strong></div>
                <div><span>Reason</span><strong>{orderStatus.reason ?? orderStatus.cancelReason ?? '—'}</strong></div>
              </div>
            )}
            {quoteQuery.isError && <p className="inline-error">Live broker pricing is required before an order can be submitted.</p>}
          </div>
        </div>

      </section>
        </div>
        )}
      </section>

      {confirmOpen && (
        <div className="modal-backdrop" onClick={() => setConfirmOpen(false)}>
          <div className="confirm-modal" onClick={(event) => event.stopPropagation()}>
            <p className="eyebrow">Write action requires confirmation</p>
            <h3>Confirm Practice order</h3>
            <p>
              Submit a {side} {orderType} order for <strong>{activeInstrument}</strong> at {activeEntryPrice.toFixed(pricePrecision)}, stop {stopLossPrice}, target {takeProfitPrice}.
            </p>
            <div className="modal-actions">
              <button className="secondary-action" onClick={() => setConfirmOpen(false)}>
                Cancel
              </button>
              <button className="primary-action" disabled={!canSubmitOrder} onClick={handleOrderSubmit}>
                Confirm Practice {orderType} order
              </button>
            </div>
          </div>
        </div>
      )}

      {closeTarget && (
        <div className="modal-backdrop" onClick={() => { if (!isClosing) setCloseTarget(null) }}>
          <div className="confirm-modal" onClick={(event) => event.stopPropagation()}>
            <p className="eyebrow">{closeTarget.source === 'auto' ? 'Automatic strategy position' : 'Manual position'}</p>
            <h3>Close {closeTarget.symbol} {closeTarget.side}?</h3>
            <p>Close all {Number(closeTarget.units).toLocaleString()} units at the broker’s current market price. This cannot be undone.</p>
            {closeError && <p className="inline-error">{closeError}</p>}
            <div className="modal-actions">
              <button className="secondary-action" disabled={isClosing} onClick={() => setCloseTarget(null)}>Cancel</button>
              <button className="danger-action" disabled={isClosing} onClick={handlePositionClose}>{isClosing ? 'Closing…' : 'Confirm close'}</button>
            </div>
          </div>
        </div>
      )}

      {(modifyPositionTarget || modifyPendingTarget) && (
        <div className="modal-backdrop" onClick={() => { if (!isModifying) { setModifyPositionTarget(null); setModifyPendingTarget(null) } }}>
          <div className="confirm-modal" onClick={(event) => event.stopPropagation()}>
            <p className="eyebrow">Broker modification</p>
            {modifyPositionTarget ? (
              <>
                <h3>Modify {modifyPositionTarget.symbol} protection</h3>
                <label className="modal-field"><span>Stop loss price</span><input type="number" min="0" step={10 ** -modifyPricePrecision} value={modifyStopLoss} onChange={(event) => setModifyStopLoss(event.target.value)} /></label>
                <label className="modal-field"><span>Trailing stop loss (pips)</span><input type="number" min="0.1" step="0.1" value={modifyTrailingStopPips} onChange={(event) => setModifyTrailingStopPips(event.target.value)} /><small className="calculated-price">OANDA distance: {modifyTrailingStopLossDistance ?? '—'} {modifyPositionQuoteQuery.data?.quoteCurrency ?? ''} ({modifyPipSize > 0 ? `1 pip = ${modifyPipSize}` : 'waiting for live instrument pip size'})</small></label>
                <label className="modal-field"><span>Take profit price</span><input type="number" min="0" step={10 ** -modifyPricePrecision} value={modifyTakeProfit} onChange={(event) => setModifyTakeProfit(event.target.value)} /></label>
                <p>A trailing stop replaces any fixed stop loss. Other blank values leave that protection unchanged. Changes are sent to OANDA, not stored locally.</p>
              </>
            ) : modifyPendingTarget ? (
              <>
                <h3>Modify pending {modifyPendingTarget.symbol} order</h3>
                <label className="modal-field"><span>Limit price</span><input type="number" min="0" step={10 ** -pricePrecision} value={modifyPendingPrice} onChange={(event) => setModifyPendingPrice(event.target.value)} /></label>
                <label className="modal-field"><span>Units</span><input type="number" min="1" step="1" value={modifyPendingUnits} onChange={(event) => setModifyPendingUnits(event.target.value)} /></label>
                <p>This updates the broker's pending order price and signed units.</p>
              </>
            ) : null}
            {(modifyError || modifyProtectionValidation) && <p className="inline-error">{modifyError ?? modifyProtectionValidation}</p>}
            <div className="modal-actions">
              <button className="secondary-action" disabled={isModifying} onClick={() => { setModifyPositionTarget(null); setModifyPendingTarget(null) }}>Cancel</button>
              <button className="primary-action" disabled={isModifying || Boolean(modifyProtectionValidation)} onClick={handleModificationSubmit}>{isModifying ? 'Updating…' : 'Apply broker changes'}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
