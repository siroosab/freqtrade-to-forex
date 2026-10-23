import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  clearCandleCache,
  downloadCandleDateRange,
  getAutoHyperoptSchedule,
  getCandleCacheInventory,
  getAvailableStrategies,
  getBacktestJob,
  getHyperoptLossFunctions,
  getHyperoptReport,
  getHyperoptReports,
  getHyperoptStatus,
  getMarketQuote,
  getSetupStatus,
  getStrategyReview,
  runBacktest,
  saveStrategyReview,
  startHyperopt,
  stopHyperopt,
} from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H2', 'H4', 'H6', 'H8', 'H12', 'D1', 'W1', 'MN1']
const EMA_TREND_TIMEPERIODS = [7, 9, 14, 19, 21, 37]

function describePair(quote: {
  pair: string
  displayName?: string
  instrumentType?: string | null
} | undefined, pair: string, isPending: boolean): string {
  if (!quote) {
    return isPending
      ? 'در حال دریافت مشخصات این جفت از کارگزار…'
      : 'مشخصات این جفت از کارگزار در دسترس نیست.'
  }
  const displayName = quote.displayName?.trim() || quote.pair || pair
  const instrumentType = quote.instrumentType?.toUpperCase()
  if (instrumentType === 'CURRENCY') return `جفت‌ارز فارکس · ${displayName}`
  if (instrumentType === 'METAL') return `فلز گران‌بها · ${displayName}`
  if (instrumentType === 'CFD') return `قرارداد CFD · ${displayName}`
  return `ابزار معاملاتی کارگزار · ${displayName}`
}

function initialDateRange(): { startDate: string; endDate: string } {
  const end = new Date()
  const start = new Date(end)
  start.setUTCDate(start.getUTCDate() - 29)
  const utcDate = (value: Date) => (
    `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, '0')}-${String(value.getUTCDate()).padStart(2, '0')}`
  )
  return {
    startDate: utcDate(start),
    endDate: utcDate(end),
  }
}

function formatDateRangeDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short',
      timeZone: 'UTC',
      year: 'numeric',
    })
}

function timeframeMinutes(value: string): number {
  if (value.toUpperCase().startsWith('MN')) {
    const months = Number(value.slice(2))
    return Number.isFinite(months) ? months * 43200 : 0
  }
  const match = /^(\d+)([mhdw])$/i.exec(value)
    ?? /^([mhdw])(\d+)$/i.exec(value)
  if (!match) return 0
  const unitFirst = /^[mhdw]/i.test(value)
  const amount = Number(match[unitFirst ? 2 : 1])
  const unit = match[unitFirst ? 1 : 2].toLowerCase()
  return amount * (unit === 'm' ? 1 : unit === 'h' ? 60 : unit === 'd' ? 1440 : 10080)
}

function formatUtcDateTime(value: string): string {
  return `${new Date(value).toLocaleString(undefined, { timeZone: 'UTC' })} UTC`
}

function formatPercent(value: string | number | undefined, showPositiveSign = false): string {
  const numericValue = Number(value)
  if (value === undefined || !Number.isFinite(numericValue)) return '—'
  return `${showPositiveSign && numericValue > 0 ? '+' : ''}${numericValue.toFixed(2)}%`
}

function metricTone(value: string | number | undefined, kind: 'return' | 'win-rate', trades?: number): string {
  const numericValue = Number(value)
  if (
    value === undefined
    || !Number.isFinite(numericValue)
    || (kind === 'win-rate' && (!trades || trades <= 0))
  ) return 'metric-neutral'
  if (kind === 'win-rate') return numericValue >= 50 ? 'metric-positive' : 'metric-negative'
  return numericValue > 0 ? 'metric-positive' : numericValue < 0 ? 'metric-negative' : 'metric-neutral'
}

function percentageOfInitialCapital(netPl?: string, startingBalance?: string): number | null {
  const profit = Number(netPl)
  const initialCapital = Number(startingBalance)
  if (!Number.isFinite(profit) || !Number.isFinite(initialCapital) || initialCapital <= 0) return null
  return profit / initialCapital * 100
}

function formatTrailingStopLoss(stopLoss: {
  mode: 'pips' | 'percent' | 'money'
  value: string
  unit?: string
} | undefined): string {
  if (!stopLoss) return 'value unavailable'
  const unit = stopLoss.unit
    ?? (stopLoss.mode === 'pips' ? 'pips' : stopLoss.mode === 'percent' ? '% of entry price' : '')
  const valueLabel = stopLoss.mode === 'money' ? 'max loss' : 'distance'
  return `${valueLabel}: ${stopLoss.value} ${unit}`.trim()
}

export function HyperoptPage() {
  const queryClient = useQueryClient()
  const pairs = useUiStore((state) => state.selectedInstruments)
  const [pair, setPair] = useState(pairs[0] ?? 'EUR/USD')
  const [timeframe, setTimeframe] = useState('M15')
  const [strategyClass, setStrategyClass] = useState('')
  const [dateRange, setDateRange] = useState(initialDateRange)
  const [attempts, setAttempts] = useState(100)
  const [workers, setWorkers] = useState<number | null>(null)
  const [lossFunction, setLossFunction] = useState('ForexWinRateProfitHyperOptLoss')
  const [spreadOverride, setSpreadOverride] = useState<{ pair: string; value: string } | null>(null)
  const [slippage, setSlippage] = useState('0')
  const [financingRatePerDay, setFinancingRatePerDay] = useState('0')
  const [commissionRatePercent, setCommissionRatePercent] = useState('0')
  const [stopLossMode, setStopLossMode] = useState<'pips' | 'percent' | 'money'>('pips')
  const [stopDistanceMode, setStopDistanceMode] = useState<'static' | 'automatic'>('automatic')
  const [trailingStopLoss, setTrailingStopLoss] = useState(true)
  const [optimizeRoi, setOptimizeRoi] = useState(true)
  const [useExitSignal, setUseExitSignal] = useState(true)
  const [emaTrendEnabled, setEmaTrendEnabled] = useState(true)
  const [emaTrendTimeframe, setEmaTrendTimeframe] = useState<'1h' | '4h' | '1d'>('1h')
  const [emaTrendTimeperiod, setEmaTrendTimeperiod] = useState(7)
  const [emaTrendTimeperiodMode, setEmaTrendTimeperiodMode] = useState<'fixed' | 'automatic'>('fixed')
  const [stopLossPips, setStopLossPips] = useState('25')
  const [backtestStopLossPipsOverride, setBacktestStopLossPipsOverride] = useState<{
    scope: string
    value: string
  } | null>(null)
  const [stopLossPercent, setStopLossPercent] = useState('0.5')
  const [stopLossMoney, setStopLossMoney] = useState('50')
  const [positionSizeMode, setPositionSizeMode] = useState<'risk' | 'units' | 'account_amount'>('account_amount')
  const [unitSize, setUnitSize] = useState('1000')
  const [accountAmount, setAccountAmount] = useState('1000')
  const [backtestJobId, setBacktestJobId] = useState<string | null>(null)
  const strategiesQuery = useQuery({
    queryKey: ['available-strategies'],
    queryFn: getAvailableStrategies,
  })
  const lossFunctionsQuery = useQuery({
    queryKey: ['hyperopt-loss-functions'],
    queryFn: getHyperoptLossFunctions,
  })
  const quoteQuery = useQuery({
    queryKey: ['hyperopt-cost-quote', pair],
    queryFn: () => getMarketQuote(pair),
    enabled: Boolean(pair),
  })
  const setupQuery = useQuery({
    queryKey: ['setup-status'],
    queryFn: getSetupStatus,
  })
  const autoHyperoptQuery = useQuery({
    queryKey: ['auto-hyperopt-schedule'],
    queryFn: getAutoHyperoptSchedule,
    refetchInterval: (query) => query.state.data?.queue.status === 'running' ? 1500 : 5000,
  })
  const cacheQuery = useQuery({
    queryKey: ['candle-cache', pair, timeframe],
    queryFn: () => getCandleCacheInventory(pair, timeframe),
    enabled: Boolean(pair && timeframe),
  })

  useEffect(() => {
    if (pairs.length && !pairs.includes(pair)) setPair(pairs[0])
  }, [pairs, pair])

  useEffect(() => {
    const strategies = strategiesQuery.data
    if (!strategies?.length) return
    setStrategyClass((current) => (
      strategies.some((item) => item.name === current)
        ? current
        : strategies.find((item) => item.name === 'ForexMasterStrategy')?.name
          ?? strategies[0].name
    ))
  }, [strategiesQuery.data])

  useEffect(() => {
    if (lossFunctionsQuery.data?.default) setLossFunction(lossFunctionsQuery.data.default)
  }, [lossFunctionsQuery.data?.default])

  const scopeEnabled = Boolean(pair && timeframe && strategyClass)
  const statusQuery = useQuery({
    queryKey: ['hyperopt-status', pair, timeframe, strategyClass],
    queryFn: () => getHyperoptStatus(pair, strategyClass, timeframe),
    enabled: scopeEnabled,
    refetchInterval: (query) => query.state.data?.status === 'running' ? 1000 : false,
  })
  const reportQuery = useQuery({
    queryKey: ['hyperopt-report', pair, timeframe, strategyClass],
    queryFn: () => getHyperoptReport(pair, strategyClass, timeframe),
    enabled: scopeEnabled,
    refetchInterval: () => statusQuery.data?.status === 'running' ? 3000 : false,
  })
  const reportsQuery = useQuery({
    queryKey: ['hyperopt-reports', pair, timeframe, strategyClass],
    queryFn: () => getHyperoptReports(pair, strategyClass, timeframe),
    enabled: scopeEnabled,
    refetchInterval: () => statusQuery.data?.status === 'running' ? 3000 : false,
  })
  const reviewQuery = useQuery({
    queryKey: ['strategy-review', pair, timeframe, strategyClass],
    queryFn: () => getStrategyReview(pair, timeframe, strategyClass),
    enabled: scopeEnabled,
  })
  useEffect(() => {
    const completedReportRevision = statusQuery.data?.status === 'completed'
      ? statusQuery.data.report?.dataRevision
      : undefined
    if (!completedReportRevision) return
    void queryClient.invalidateQueries({
      queryKey: ['strategy-review', pair, timeframe, strategyClass],
    })
    void queryClient.invalidateQueries({
      queryKey: ['hyperopt-report', pair, timeframe, strategyClass],
    })
    void queryClient.invalidateQueries({
      queryKey: ['hyperopt-reports', pair, timeframe, strategyClass],
    })
  }, [pair, queryClient, statusQuery.data?.report?.dataRevision, statusQuery.data?.status, strategyClass, timeframe])
  useEffect(() => {
    const revision = reviewQuery.data?.approvedRevision
    if (
      !revision
      || revision.pair !== pair
      || revision.timeframe.toUpperCase() !== timeframe.toUpperCase()
      || revision.strategyClass !== strategyClass
    ) return
    const runSettings = revision.hyperopt?.runSettings
    const condition = revision.hyperopt?.emaTrendCondition
      ?? runSettings?.emaTrendCondition
    if (!condition) return
    setEmaTrendEnabled(condition.enabled)
    setEmaTrendTimeframe(condition.timeframe)
    setEmaTrendTimeperiod(
      EMA_TREND_TIMEPERIODS.includes(condition.timeperiod)
        ? condition.timeperiod
        : 7,
    )
    setEmaTrendTimeperiodMode(runSettings?.emaTrendTimeperiodMode === 'automatic' ? 'automatic' : 'fixed')
  }, [pair, reviewQuery.data?.approvedRevision, strategyClass, timeframe])
  const backtestQuery = useQuery({
    queryKey: ['backtest-job', backtestJobId],
    queryFn: () => getBacktestJob(backtestJobId ?? ''),
    enabled: Boolean(backtestJobId),
    refetchInterval: (query) => (
      query.state.data?.status === 'completed' || query.state.data?.status === 'failed'
        ? false
        : 1000
    ),
  })

  const hyperoptMutation = useMutation({
    mutationFn: startHyperopt,
    onSuccess: () => {
      void statusQuery.refetch()
      void reportQuery.refetch()
      void queryClient.invalidateQueries({ queryKey: ['candle-cache', pair, timeframe] })
    },
  })
  const stopMutation = useMutation({
    mutationFn: () => stopHyperopt(pair, strategyClass, timeframe),
    onSuccess: () => void statusQuery.refetch(),
  })
  const backtestMutation = useMutation({
    mutationFn: runBacktest,
    onSuccess: (job) => {
      setBacktestJobId(job.id)
      if (job.status === 'completed') void backtestQuery.refetch()
    },
  })
  const reviewMutation = useMutation({
    mutationFn: saveStrategyReview,
    onSuccess: async () => {
      await Promise.all([
        reviewQuery.refetch(),
        reportsQuery.refetch(),
      ])
    },
  })
  const downloadMutation = useMutation({
    mutationFn: downloadCandleDateRange,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['candle-cache', pair, timeframe] })
    },
  })
  const clearCacheMutation = useMutation({
    mutationFn: () => clearCandleCache(pair, timeframe),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['candle-cache', pair, timeframe] })
    },
  })

  const report = statusQuery.data?.report ?? reportQuery.data?.report
  const availableWorkers = statusQuery.data?.availableWorkers
    ?? autoHyperoptQuery.data?.availableWorkers
    ?? 1
  const selectedWorkers = Math.min(workers ?? availableWorkers, availableWorkers)
  const approvedRevision = reviewQuery.data?.approvedRevision
  const approvedHyperopt = approvedRevision?.hyperopt
  const approvedRevisionMatchesScope = Boolean(
    approvedRevision
    && approvedRevision.pair.replace('_', '/').toUpperCase() === pair.replace('_', '/').toUpperCase()
    && approvedRevision.timeframe.toUpperCase() === timeframe.toUpperCase()
    && approvedRevision.strategyClass === strategyClass,
  )
  const approvedDataRevision = approvedRevisionMatchesScope
    && typeof approvedHyperopt?.dataRevision === 'string'
    ? approvedHyperopt.dataRevision
    : undefined
  const historyReports = reportsQuery.data?.reports ?? []
  const latestApprovedReport = historyReports.find(
    (entry) => entry.report.dataRevision === approvedDataRevision,
  )
  const latestUnapprovedReport = historyReports.find(
    (entry) => entry.report.status === 'completed'
      && entry.report.dataRevision !== approvedDataRevision,
  )
  const approvedTrailingStopLossMatches = Boolean(
    approvedRevision
    && approvedRevision.pair === pair
    && approvedRevision.timeframe.toUpperCase() === timeframe.toUpperCase()
    && approvedRevision.strategyClass === strategyClass
    && approvedHyperopt?.trailingStopLoss === true,
  )
  const reportStopLossPips = report?.status === 'completed'
    && report.stopLoss?.mode === 'pips'
    && Number.isFinite(Number(report.stopLoss.value))
    && Number(report.stopLoss.value) > 0
    ? report.stopLoss.value
    : null
  const backtestStopLossScope = `${pair}|${timeframe}|${strategyClass}|${report?.dataRevision ?? 'no-report'}`
  const backtestStopLossPips = backtestStopLossPipsOverride?.scope === backtestStopLossScope
    ? backtestStopLossPipsOverride.value
    : reportStopLossPips ?? stopLossPips
  const backtest = backtestQuery.data?.result ?? backtestQuery.data
  const backtestReturn = percentageOfInitialCapital(backtest?.netPl, backtest?.startingBalance)
  const running = statusQuery.data?.status === 'running'
  const autoHyperoptRunning = autoHyperoptQuery.data?.queue.status === 'running'
    || autoHyperoptQuery.data?.queue.status === 'queued'
  const backtestRunning = Boolean(
    backtestJobId
    && backtestQuery.data?.status !== 'completed'
    && backtestQuery.data?.status !== 'failed',
  )
  const researchBusy = running
    || backtestRunning
    || hyperoptMutation.isPending
    || backtestMutation.isPending
    || downloadMutation.isPending
    || clearCacheMutation.isPending
    || autoHyperoptRunning
  const strategies = strategiesQuery.data ?? []
  const dateRangeValid = Boolean(dateRange.startDate && dateRange.endDate)
    && dateRange.startDate <= dateRange.endDate
  const selectedDateCount = dateRangeValid
    ? Math.floor(
      (Date.parse(`${dateRange.endDate}T00:00:00Z`) - Date.parse(`${dateRange.startDate}T00:00:00Z`))
      / 86_400_000,
    ) + 1
    : 0
  const effectiveSpread = spreadOverride?.pair === pair
    ? spreadOverride.value
    : quoteQuery.data?.spread ?? ''
  const costValues = [effectiveSpread, slippage, financingRatePerDay, commissionRatePercent]
  const costSettingsValid = costValues.every((value) => value.trim() !== ''
    && Number.isFinite(Number(value))
    && Number(value) >= 0)
  const selectedPositionSize = positionSizeMode === 'units' ? unitSize : accountAmount
  const positionSizeValid = positionSizeMode === 'risk'
    || (selectedPositionSize.trim() !== ''
      && Number.isFinite(Number(selectedPositionSize))
      && Number(selectedPositionSize) > 0)
  const riskFraction = setupQuery.data?.riskFraction ?? '0.01'
  const stopLossValue = stopLossMode === 'pips'
    ? stopLossPips
    : stopLossMode === 'percent'
      ? stopLossPercent
      : stopLossMoney
  const stopLossValid = Number.isFinite(Number(stopLossValue)) && Number(stopLossValue) > 0
  const backtestStopLossValue = stopLossMode === 'pips'
    ? backtestStopLossPips
    : stopLossValue
  const backtestStopLossValid = Number.isFinite(Number(backtestStopLossValue))
    && Number(backtestStopLossValue) > 0
  const hyperoptStopLossValid = stopDistanceMode === 'automatic' || stopLossValid
  const emaTrendTimeframeValid = !emaTrendEnabled
    || timeframeMinutes(emaTrendTimeframe) > timeframeMinutes(timeframe)
  const emaTrendCondition = {
    enabled: emaTrendEnabled,
    timeframe: emaTrendTimeframe,
    timeperiod: emaTrendTimeperiod,
  } as const

  useEffect(() => {
    if (backtestQuery.data?.status === 'completed') {
      void queryClient.invalidateQueries({ queryKey: ['candle-cache', pair, timeframe] })
    }
  }, [backtestQuery.data?.status, pair, queryClient, timeframe])

  const runResearch = () => {
    const history = {
      historyMode: 'date_range' as const,
      historyValue: 0,
      steps: 0,
      ...dateRange,
    }
    backtestMutation.mutate({
      pair,
      timeframe,
      strategyClass,
      ...history,
      spread: effectiveSpread,
      slippage,
      financingRatePerDayPercent: financingRatePerDay,
      commissionRatePercent,
      positionSizeMode,
      positionSize: positionSizeMode === 'risk' ? null : selectedPositionSize,
      stopLossMode,
      stopLossValue: backtestStopLossValue,
      emaTrendCondition,
    })
  }

  const runOptimization = () => {
    hyperoptMutation.mutate({
      pair,
      timeframe,
      strategyClass,
      steps: 0,
      historyMode: 'date_range',
      historyValue: 0,
      ...dateRange,
      attempts,
      workers: selectedWorkers,
      hyperoptLoss: lossFunction,
      spread: effectiveSpread,
      slippage,
      financingRatePerDayPercent: financingRatePerDay,
      commissionRatePercent,
      positionSizeMode,
      positionSize: selectedPositionSize,
      stopDistanceMode,
      trailingStopLoss,
      optimizeRoi,
      useExitSignal,
      optimizeEmaTrendTimeperiod: emaTrendEnabled && emaTrendTimeperiodMode === 'automatic',
      emaTrendCondition,
      stopLossMode: stopDistanceMode === 'automatic' ? 'pips' : stopLossMode,
      stopLossValue: stopDistanceMode === 'automatic' ? stopLossPips : stopLossValue,
    })
  }

  const saveReview = (dataRevision?: string) => {
    reviewMutation.mutate({
      status: 'approved',
      pair,
      timeframe,
      strategyClass,
      requireOptimization: true,
      ...(dataRevision ? { dataRevision } : {}),
      notes: 'Hyperopt result approved for the selected pair, timeframe, and strategy.',
    })
  }

  const clearSelectedCache = () => {
    if (!window.confirm(`Clear all cached ${timeframe} candles for ${pair}?`)) return
    clearCacheMutation.mutate()
  }

  const timeframeOptions = TIMEFRAMES

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">STRATEGY OPTIMIZATION</p>
          <h2>Hyperopt &amp; approval</h2>
        </div>
        <span className="pill neutral">Approval required before execution</span>
      </header>

      {autoHyperoptRunning && (
        <section className="auto-hyperopt-running" role="status" aria-live="polite">
          <span className="auto-hyperopt-pulse" />
          <div>
            <strong>
              Scheduled Hyperopt is running
              {autoHyperoptQuery.data?.queue.activePair
                ? ` · ${autoHyperoptQuery.data.queue.activePair}`
                : ''}
            </strong>
            <p>Manual Hyperopt and Backtests are disabled until the automatic queue finishes.</p>
          </div>
        </section>
      )}
      {autoHyperoptQuery.isError && (
        <p className="auto-hyperopt-status-warning" role="status">
          Automatic queue status could not be checked. Server-side safeguards remain active.
        </p>
      )}

      <section className="content-grid">
        <div className="panel hyperopt-scope-panel">
          <div className="panel-header compact">
            <div><p className="eyebrow">🧭 Scope</p><h3>Choose research inputs</h3></div>
          </div>
          <div className="settings-grid hyperopt-scope-fields">
            <label className="field-block">
              <span>💱 Pair</span>
              <select value={pair} onChange={(event) => setPair(event.target.value)}>
                {(pairs.length ? pairs : ['EUR/USD']).map((item) => <option key={item}>{item}</option>)}
              </select>
              <small className="pair-description" aria-live="polite">
                {describePair(quoteQuery.data, pair, quoteQuery.isPending)}
              </small>
            </label>
            <label className="field-block">
              <span>⏱️ Timeframe</span>
              <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
                {timeframeOptions.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <label className="field-block">
              <span>🧠 Strategy</span>
              <select value={strategyClass} onChange={(event) => setStrategyClass(event.target.value)}>
                {strategies.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
              </select>
            </label>
          </div>
          <section className="hyperopt-date-range" aria-labelledby="hyperopt-date-range-title">
            <div className="hyperopt-date-range-heading">
              <div>
                <h4 id="hyperopt-date-range-title">📅 Research date range</h4>
                <p>Choose the UTC candle history used by Backtest and Hyperopt.</p>
              </div>
              <span className="hyperopt-date-range-timezone">🌐 UTC</span>
            </div>
            <div className="hyperopt-date-fields">
              <label className="field-block">
                <span>🟢 Start date</span>
                <input
                  type="date"
                  value={dateRange.startDate}
                  max={dateRange.endDate || undefined}
                  onChange={(event) => setDateRange((current) => ({ ...current, startDate: event.target.value }))}
                />
                <small>First UTC day included</small>
              </label>
              <label className="field-block">
                <span>🏁 End date</span>
                <input
                  type="date"
                  value={dateRange.endDate}
                  min={dateRange.startDate || undefined}
                  onChange={(event) => setDateRange((current) => ({ ...current, endDate: event.target.value }))}
                />
                <small>Last UTC day included</small>
              </label>
            </div>
            <div className="hyperopt-date-range-summary" aria-live="polite">
              <span>🗓️ Selected window</span>
              {dateRangeValid ? (
                <>
                  <strong>{formatDateRangeDate(dateRange.startDate)} <span aria-hidden="true">→</span> {formatDateRangeDate(dateRange.endDate)}</strong>
                  <small>{selectedDateCount.toLocaleString()} calendar days · both dates included</small>
                </>
              ) : (
                <strong className="hyperopt-date-range-invalid">Choose a valid start and end date.</strong>
              )}
            </div>
          </section>
          <section className="hyperopt-cache-summary" aria-label="Cached candle data">
            <div className="hyperopt-cache-heading">
              <strong>🧊 Cached {timeframe} candles · {pair}</strong>
              {cacheQuery.isLoading ? (
                <span>Checking cache…</span>
              ) : cacheQuery.data ? (
                <span>
                  {cacheQuery.data.candles.toLocaleString()} candles
                  {' · '}{cacheQuery.data.cachedRanges} cached range{cacheQuery.data.cachedRanges === 1 ? '' : 's'}
                </span>
              ) : (
                <span>No cache information available</span>
              )}
            </div>
            {cacheQuery.data?.from && cacheQuery.data.to && (
              <small className="hyperopt-cache-coverage">
                📍 Coverage: {formatUtcDateTime(cacheQuery.data.from)} – {formatUtcDateTime(cacheQuery.data.to)}
              </small>
            )}
            {cacheQuery.data?.ranges.map((range) => (
              <small className="hyperopt-cache-range" key={range.key}>
                {range.kind === 'range' ? '📆 Date range' : '🕘 Recent cache'} · {range.candles.toLocaleString()} candles
                {range.from && range.to
                  ? ` · ${formatUtcDateTime(range.from)} – ${formatUtcDateTime(range.to)}`
                  : ''}
              </small>
            ))}
          </section>
          {cacheQuery.error && <p role="alert">Cache inventory unavailable: {cacheQuery.error.message}</p>}
          {downloadMutation.error && <p role="alert">Data download failed: {downloadMutation.error.message}</p>}
          {downloadMutation.data && (
            <p aria-live="polite">
              Downloaded {downloadMutation.data.candles.toLocaleString()} {downloadMutation.data.timeframe} candles for {downloadMutation.data.pair}
              {' '}({downloadMutation.data.startDate} through {downloadMutation.data.endDate}; available through {formatUtcDateTime(downloadMutation.data.effectiveEnd)}).
            </p>
          )}
          {clearCacheMutation.error && <p role="alert">Cache clear failed: {clearCacheMutation.error.message}</p>}
          {clearCacheMutation.data && (
            <p aria-live="polite">Removed {clearCacheMutation.data.removedRanges} cached range(s) for {clearCacheMutation.data.pair} · {clearCacheMutation.data.timeframe}.</p>
          )}
          {(strategiesQuery.isError || lossFunctionsQuery.isError) && (
            <p role="alert">Optimization inputs could not be loaded from the API.</p>
          )}
          {!strategies.length && !strategiesQuery.isLoading && (
            <p role="alert">No strategies are available. Add a valid strategy before starting research.</p>
          )}
          <div className="hyperopt-scope-actions">
            <button
              className="primary-action"
              type="button"
              onClick={() => downloadMutation.mutate({ pair, timeframe, ...dateRange })}
              disabled={!scopeEnabled || !dateRangeValid || researchBusy}
            >
              {downloadMutation.isPending ? '⏳ Downloading candles…' : '⬇️ Download selected dates'}
            </button>
            <button
              className="secondary-action"
              type="button"
              onClick={clearSelectedCache}
              disabled={!cacheQuery.data?.cachedRanges || researchBusy}
            >
              {clearCacheMutation.isPending ? '⏳ Clearing cache…' : '🧹 Clear pair & timeframe cache'}
            </button>
          </div>
        </div>

        <div className="panel research-panel">
          <div className="panel-header compact">
            <div><p className="eyebrow">Research · 01</p><h3>Backtest</h3></div>
            <button className="primary-action" type="button" onClick={runResearch} disabled={!scopeEnabled || !dateRangeValid || !costSettingsValid || !positionSizeValid || !backtestStopLossValid || !emaTrendTimeframeValid || quoteQuery.isPending || quoteQuery.isError || setupQuery.isPending || setupQuery.isError || researchBusy}>
              {backtestMutation.isPending || backtestRunning ? 'Backtest running…' : 'Run backtest'}
            </button>
          </div>
          <p>{pair} · {timeframe} · {strategyClass || 'Select a strategy'}</p>
          <div className="settings-grid">
            <label className="field-block">
              <span>Spread (price units)</span>
              <input
                type="number"
                min="0"
                step="any"
                value={effectiveSpread}
                onChange={(event) => setSpreadOverride({ pair, value: event.target.value })}
              />
              <small>Default: current broker spread ({quoteQuery.data?.spread ?? 'loading'}). Applied as half-spread at entry and exit.</small>
            </label>
            <label className="field-block">
              <span>Slippage (price units)</span>
              <input type="number" min="0" step="any" value={slippage} onChange={(event) => setSlippage(event.target.value)} />
              <small>Default: 0. Applied adversely at entry and exit.</small>
            </label>
            <label className="field-block">
              <span>Financing rate per day (%)</span>
              <input type="number" min="0" step="any" value={financingRatePerDay} onChange={(event) => setFinancingRatePerDay(event.target.value)} />
              <small>Default: 0. Debited from longs and credited to shorts.</small>
            </label>
            <label className="field-block">
              <span>Commission per side (%)</span>
              <input type="number" min="0" step="any" value={commissionRatePercent} onChange={(event) => setCommissionRatePercent(event.target.value)} />
              <small>Default: 0. Charged on notional at entry and exit.</small>
            </label>
            <label className="field-block">
              <span>Initial position sizing</span>
              <select value={positionSizeMode} onChange={(event) => setPositionSizeMode(event.target.value as typeof positionSizeMode)}>
                <option value="risk">Risk-based (default)</option>
                <option value="units">Fixed units</option>
                <option value="account_amount">Fixed amount in account currency</option>
              </select>
              <small>Default: risk-based, {Number(riskFraction) * 100}% of account balance. Size follows the selected pip/percent stop; money stops use the default 25-pip sizing distance.</small>
            </label>
            {positionSizeMode === 'units' && (
              <label className="field-block">
                <span>Initial size (units)</span>
                <input type="number" min="1" step="1" value={unitSize} onChange={(event) => setUnitSize(event.target.value)} />
                <small>Default: 1,000 units. Rounded down to instrument precision.</small>
              </label>
            )}
            {positionSizeMode === 'account_amount' && (
              <label className="field-block">
                <span>Initial notional ({quoteQuery.data?.accountCurrency ?? 'account currency'})</span>
                <input type="number" min="0.01" step="any" value={accountAmount} onChange={(event) => setAccountAmount(event.target.value)} />
                <small>Default: 1,000 {quoteQuery.data?.accountCurrency ?? 'account currency'}, converted to units using live currency conversion.</small>
              </label>
            )}
            <label className="field-block">
              <span>
                {approvedTrailingStopLossMatches
                  ? 'Stop distance (approved trailing stop overrides this value)'
                  : 'Static stop loss'}
              </span>
              <select value={stopLossMode} onChange={(event) => setStopLossMode(event.target.value as typeof stopLossMode)}>
                <option value="pips">Pips</option>
                <option value="percent">Percent of entry price</option>
                <option value="money">Money ({quoteQuery.data?.accountCurrency ?? 'account currency'})</option>
              </select>
              <small>Static per-trade setting; not optimized. Defaults: 25 pips, 0.5% of entry price, or 50 {quoteQuery.data?.accountCurrency ?? 'account currency'}.</small>
            </label>
            <label className="field-block">
              <span>
                {stopLossMode === 'pips' ? 'Stop distance (pips)' : stopLossMode === 'percent' ? 'Stop distance (% of entry)' : `Maximum price-loss (${quoteQuery.data?.accountCurrency ?? 'account currency'})`}
              </span>
              <input
                type="number"
                min="0.000001"
                step="any"
                value={stopLossMode === 'pips' ? backtestStopLossPips : stopLossValue}
                onChange={(event) => {
                  if (stopLossMode === 'pips') {
                    setBacktestStopLossPipsOverride({
                      scope: backtestStopLossScope,
                      value: event.target.value,
                    })
                  }
                  else if (stopLossMode === 'percent') setStopLossPercent(event.target.value)
                  else setStopLossMoney(event.target.value)
                }}
              />
              <small>
                {approvedTrailingStopLossMatches && approvedHyperopt?.stopLoss
                  ? `This Backtest uses the approved Hyperopt trailing stop: ${formatTrailingStopLoss(approvedHyperopt.stopLoss)}.`
                  : stopLossMode === 'pips' && reportStopLossPips
                  ? `Default from Hyperopt result: ${reportStopLossPips} pips.`
                  : stopLossMode === 'money'
                  ? 'Default: 50. Converted to a price distance after position size and currency conversion; commissions and financing are additional costs.'
                  : `Default: ${stopLossMode === 'pips' ? '25 pips' : '0.5% of entry price'}.`}
                {stopLossMode === 'money' && positionSizeMode === 'risk' ? ' Risk-based volume still uses the default 25-pip sizing distance.' : ''}
              </small>
            </label>
          </div>
          {quoteQuery.error && <p role="alert">Could not load the default broker spread: {quoteQuery.error.message}</p>}
          {setupQuery.error && <p role="alert">Could not load the account risk default: {setupQuery.error.message}</p>}
          {backtest && (
            <div className="bullet-list">
              <div><span>Status</span><strong>{backtest.status}</strong></div>
              <div><span>Net P/L</span><strong>{backtest.netPl ?? '—'}</strong></div>
              <div><span>Trades</span><strong>{backtest.trades ?? '—'}</strong></div>
              <div>
                <span>Win rate</span>
                <strong className={`metric-value ${metricTone(backtest.winRate, 'win-rate', backtest.trades)}`}>
                  {formatPercent(backtest.winRate)}
                </strong>
              </div>
              <div>
                <span>Return on initial capital</span>
                <strong className={`metric-value ${metricTone(backtestReturn ?? undefined, 'return')}`}>
                  {formatPercent(backtestReturn ?? undefined, true)}
                </strong>
              </div>
              <div><span>Max drawdown</span><strong>{backtest.maxDrawdown ?? '—'}</strong></div>
              {backtest.execution && (
                <div><span>Applied assumptions</span><strong>
                  Spread {backtest.execution.spread} · Slippage {backtest.execution.slippage} · Financing {backtest.execution.financingRatePerDayPercent}%/day · Commission {backtest.execution.commissionRatePercent}%/side · Size {backtest.execution.positionSizeMode === 'risk' ? `risk-based (${Number(backtest.execution.riskFraction) * 100}%)` : `${backtest.execution.positionSize} ${backtest.execution.positionSizeUnit}`}
                </strong></div>
              )}
              {backtest.execution && (
                <div>
                  <span>{backtest.execution.trailingStopLoss ? 'Trailing stop loss' : 'Static stop loss'}</span>
                  <strong>
                    {backtest.execution.trailingStopLoss
                      ? `Enabled · ${backtest.execution.stopLossValue} ${backtest.execution.stopLossUnit}`
                      : 'Disabled'}
                    {backtest.execution.configSource === 'approved-hyperopt'
                      ? ' · approved Hyperopt'
                      : ' · Backtest settings'}
                    {backtest.execution.stopLossOptimized ? ' · optimized' : ''}
                  </strong>
                </div>
              )}
              {backtest.execution && (
                <div>
                  <span>Approved exit behavior</span>
                  <strong>
                    ROI {backtest.execution.useRoi ? 'enabled' : 'disabled'} · strategy exit signals {backtest.execution.useExitSignal ? 'enabled' : 'disabled'}
                    {backtest.execution.configSource === 'approved-hyperopt' ? ' · approved Hyperopt' : ''}
                  </strong>
                </div>
              )}
              {backtest.execution && (
                <div>
                  <span>EMA trend condition</span>
                  <strong>
                    {backtest.execution.emaTrendCondition.enabled
                      ? `Enabled · ${backtest.execution.emaTrendCondition.timeframe.toUpperCase()} · period ${backtest.execution.emaTrendCondition.timeperiod}`
                      : 'Disabled'}
                    {backtest.execution.configSource === 'approved-hyperopt'
                      ? ' · approved Hyperopt'
                      : ''}
                  </strong>
                </div>
              )}
              {backtest.warning && <div><span>Data note</span><strong>{backtest.warning}</strong></div>}
            </div>
          )}
          {backtestQuery.data?.status === 'running' && (
            <p aria-live="polite">{backtestQuery.data.message} History {backtestQuery.data.historyProgress ?? 0}% · backtest {backtestQuery.data.backtestProgress ?? 0}%</p>
          )}
          {backtestQuery.data?.status === 'failed' && <p role="alert">Backtest failed: {backtestQuery.data.message}</p>}
          {backtestMutation.error && <p role="alert">Backtest request failed: {backtestMutation.error.message}</p>}
          {backtestQuery.error && <p role="alert">Backtest status failed: {backtestQuery.error.message}</p>}
        </div>

        <div className="panel research-panel hyperopt-optimization-panel">
          <div className="panel-header compact hyperopt-panel-header">
            <div>
              <p className="eyebrow">⚙️ Research · 02</p>
              <h3>🧠 Optimize strategy parameters</h3>
              <p className="hyperopt-panel-intro">Find stronger settings using historical market data and your selected objective.</p>
            </div>
            <span className="pill neutral">✨ Manual Hyperopt</span>
          </div>
          <div className="settings-grid">
            <label className="field-block">
              <span>Spread (price units)</span>
              <input type="number" min="0" step="any" value={effectiveSpread} onChange={(event) => setSpreadOverride({ pair, value: event.target.value })} />
              <small>Default: current broker spread ({quoteQuery.data?.spread ?? 'loading'}).</small>
            </label>
            <label className="field-block">
              <span>Slippage (price units)</span>
              <input type="number" min="0" step="any" value={slippage} onChange={(event) => setSlippage(event.target.value)} />
              <small>Default: 0.</small>
            </label>
            <label className="field-block">
              <span>Financing rate per day (%)</span>
              <input type="number" min="0" step="any" value={financingRatePerDay} onChange={(event) => setFinancingRatePerDay(event.target.value)} />
              <small>Default: 0.</small>
            </label>
            <label className="field-block">
              <span>Commission per side (%)</span>
              <input type="number" min="0" step="any" value={commissionRatePercent} onChange={(event) => setCommissionRatePercent(event.target.value)} />
              <small>Default: 0.</small>
            </label>
            <label className="field-block">
              <span>Initial position sizing</span>
              <select value={positionSizeMode} onChange={(event) => setPositionSizeMode(event.target.value as typeof positionSizeMode)}>
                <option value="risk">Risk-based (default)</option>
                <option value="units">Fixed units</option>
                <option value="account_amount">Fixed amount in account currency</option>
              </select>
              <small>Default: risk-based, {Number(riskFraction) * 100}% of account balance. Size follows the selected pip/percent stop; money stops use the default 25-pip sizing distance.</small>
            </label>
            {positionSizeMode === 'units' && (
              <label className="field-block">
                <span>Initial size (units)</span>
                <input type="number" min="1" step="1" value={unitSize} onChange={(event) => setUnitSize(event.target.value)} />
                <small>Default: 1,000 units.</small>
              </label>
            )}
            {positionSizeMode === 'account_amount' && (
              <label className="field-block">
                <span>Initial notional ({quoteQuery.data?.accountCurrency ?? 'account currency'})</span>
                <input type="number" min="0.01" step="any" value={accountAmount} onChange={(event) => setAccountAmount(event.target.value)} />
                <small>Default: 1,000 {quoteQuery.data?.accountCurrency ?? 'account currency'}.</small>
              </label>
            )}
            <div className="hyperopt-stop-distance-group">
              <div className="hyperopt-stop-distance-heading">
                <span aria-hidden="true">🛡️</span>
                <div>
                  <strong>Stop distance</strong>
                  <small>Choose how the protective stop is set for this optimization.</small>
                </div>
              </div>
              <div className="hyperopt-stop-distance-fields">
                <label className="field-block">
                  <span>⚙️ Stop distance mode</span>
                  <select value={stopDistanceMode} onChange={(event) => setStopDistanceMode(event.target.value as typeof stopDistanceMode)}>
                    <option value="static">Static</option>
                    <option value="automatic">Automatic (optimize with Hyperopt)</option>
                  </select>
                  <small>Automatic mode searches from 0.5× to 3× the training candles' median true range (minimum 0.1 pip) and saves the best result with the approved strategy.</small>
                </label>
                {stopDistanceMode === 'static' ? (
                  <>
                    <label className="field-block">
                      <span>📏 Static stop loss unit</span>
                      <select value={stopLossMode} onChange={(event) => setStopLossMode(event.target.value as typeof stopLossMode)}>
                        <option value="pips">Pips</option>
                        <option value="percent">Percent of entry price</option>
                        <option value="money">Money ({quoteQuery.data?.accountCurrency ?? 'account currency'})</option>
                      </select>
                      <small>Fixed per-trade stop distance; this value is not optimized.</small>
                    </label>
                    <label className="field-block">
                      <span>
                        🎯 {stopLossMode === 'pips' ? 'Stop distance (pips)' : stopLossMode === 'percent' ? 'Stop distance (% of entry)' : `Maximum price-loss (${quoteQuery.data?.accountCurrency ?? 'account currency'})`}
                      </span>
                      <input
                        type="number"
                        min="0.000001"
                        step="any"
                        value={stopLossValue}
                        onChange={(event) => {
                          if (stopLossMode === 'pips') setStopLossPips(event.target.value)
                          else if (stopLossMode === 'percent') setStopLossPercent(event.target.value)
                          else setStopLossMoney(event.target.value)
                        }}
                      />
                      <small>
                        {stopLossMode === 'money'
                          ? 'Default: 50. Converted to a price distance after position size and currency conversion; commissions and financing are additional costs.'
                          : `Default: ${stopLossMode === 'pips' ? '25 pips' : '0.5% of entry price'}.`}
                        {stopLossMode === 'money' && positionSizeMode === 'risk' ? ' Risk-based volume uses the default 25-pip sizing distance.' : ''}
                      </small>
                    </label>
                  </>
                ) : (
                  <div className="field-block hyperopt-stop-distance-auto">
                    <span>✨ Stop distance (pips)</span>
                    <strong>Selected automatically by Hyperopt for this pair</strong>
                  </div>
                )}
              </div>
            </div>
            <label className="field-block">
              <span>Trailing stop loss</span>
              <input
                className="trailing-stop-checkbox"
                type="checkbox"
                checked={trailingStopLoss}
                onChange={(event) => setTrailingStopLoss(event.target.checked)}
              />
              <small>
                When enabled, Hyperopt evaluates a trailing stop using the selected stop distance. Approved trades use OANDA's native trailing-stop order; when disabled, the fixed stop loss remains in effect.
              </small>
            </label>
            <label className="field-block">
              <span>Include ROI in optimization</span>
              <input
                className="trailing-stop-checkbox"
                type="checkbox"
                checked={optimizeRoi}
                onChange={(event) => setOptimizeRoi(event.target.checked)}
              />
              <small>
                When enabled, Hyperopt searches ROI targets and includes the approved ROI schedule in Backtest and live trading. Disabled means ROI exits are off, regardless of the strategy default.
              </small>
            </label>
            <label className="field-block">
              <span>Use strategy exit signals</span>
              <input
                className="trailing-stop-checkbox"
                type="checkbox"
                checked={useExitSignal}
                onChange={(event) => setUseExitSignal(event.target.checked)}
              />
              <small>
                When enabled, signal-based and explicit strategy exits are evaluated. The approved setting overrides the strategy default in Backtest and live trading.
              </small>
            </label>
            <section className="hyperopt-ema-condition-group" aria-labelledby="hyperopt-ema-condition-title">
              <div className="hyperopt-ema-condition-heading">
                <div>
                  <h4 id="hyperopt-ema-condition-title">📈 EMA higher-timeframe trend condition</h4>
                  <p>Filter strategy entries using the completed higher-timeframe candle data.</p>
                </div>
                <label className="hyperopt-ema-condition-toggle">
                  <span>Enable filter</span>
                  <input
                    className="trailing-stop-checkbox"
                    type="checkbox"
                    checked={emaTrendEnabled}
                    onChange={(event) => setEmaTrendEnabled(event.target.checked)}
                  />
                </label>
              </div>
              {emaTrendEnabled && (
                <div className="hyperopt-ema-condition-fields">
                  <label className="field-block">
                    <span>EMA trend timeframe</span>
                    <select
                      value={emaTrendTimeframe}
                      onChange={(event) => setEmaTrendTimeframe(event.target.value as '1h' | '4h' | '1d')}
                    >
                      <option value="1h">1 hour (H1)</option>
                      <option value="4h">4 hours (H4)</option>
                      <option value="1d">1 day (D1)</option>
                    </select>
                    <small>The condition timeframe must be higher than the strategy timeframe.</small>
                  </label>
                  <label className="field-block">
                    <span>🔗 EMA timeperiod</span>
                    <select
                      value={emaTrendTimeperiodMode === 'automatic' ? 'automatic' : String(emaTrendTimeperiod)}
                      onChange={(event) => {
                        if (event.target.value === 'automatic') {
                          setEmaTrendTimeperiodMode('automatic')
                        } else {
                          setEmaTrendTimeperiodMode('fixed')
                          setEmaTrendTimeperiod(Number(event.target.value))
                        }
                      }}
                    >
                      <option value="automatic">✨ Automatic — optimize with Hyperopt</option>
                      {EMA_TREND_TIMEPERIODS.map((period) => (
                        <option key={period} value={period}>{period}</option>
                      ))}
                    </select>
                    <small>
                      {emaTrendTimeperiodMode === 'automatic'
                        ? 'Hyperopt tests periods 7, 9, 14, 19, 21, and 37 in randomized order; six or more attempts test every value and select the best-performing EMA period.'
                        : 'Long entries require main-timeframe low above EMA(high, period); shorts require main-timeframe high below EMA(low, period).'}
                    </small>
                  </label>
                </div>
              )}
            </section>
            {!emaTrendTimeframeValid && (
              <p className="field-block" role="alert">
                Select H1, H4, or D1 above the strategy timeframe, or disable the EMA condition.
              </p>
            )}
            <label className="field-block">
              <span>Attempts</span>
              <input type="number" min="1" max="900" value={attempts} onChange={(event) => setAttempts(Number(event.target.value))} />
            </label>
            <label className="field-block">
              <span>CPU worker processes</span>
              <select value={selectedWorkers} onChange={(event) => setWorkers(Number(event.target.value))}>
                {Array.from({ length: availableWorkers }, (_, index) => index + 1).map((count) => (
                  <option key={count} value={count}>{count}</option>
                ))}
              </select>
              <small>
                Available to Hyperopt: {availableWorkers}. One CPU is reserved for live trading and the UI when possible; a single-CPU server can use one worker.
              </small>
            </label>
            <label className="field-block">
              <span>Objective</span>
              <select value={lossFunction} onChange={(event) => setLossFunction(event.target.value)}>
                {(lossFunctionsQuery.data?.options ?? [lossFunction]).map((item) => <option key={item}>{item}</option>)}
              </select>
              <small>
                Validation score = 0.6 × win-rate% + 8 × return% − 1.5 × drawdown% − 0.5 per missing trade below 10.
              </small>
            </label>
          </div>
          {quoteQuery.error && <p role="alert">Could not load the default broker spread: {quoteQuery.error.message}</p>}
          {setupQuery.error && <p role="alert">Could not load the account risk default: {setupQuery.error.message}</p>}
          {running && statusQuery.data && (
            <p aria-live="polite">Progress: {statusQuery.data.attemptsCompleted} / {statusQuery.data.attemptsTotal} attempts</p>
          )}
          {statusQuery.data?.error && <p role="alert">Hyperopt failed: {statusQuery.data.error}</p>}
          {hyperoptMutation.error && <p role="alert">Hyperopt request failed: {hyperoptMutation.error.message}</p>}
          {statusQuery.error && <p role="alert">Hyperopt status failed: {statusQuery.error.message}</p>}
          {stopMutation.error && <p role="alert">Stop request failed: {stopMutation.error.message}</p>}
          <div className="hyperopt-action-bar">
            <div className="hyperopt-action-copy">
              <strong>{running ? '🔄 Optimization in progress' : '🚀 Ready to optimize?'}</strong>
              <small>
                {running
                  ? 'Stop the current run if you need to change course.'
                  : 'Start a run with the selected settings, or stop an active run.'}
              </small>
            </div>
            <div className="hyperopt-action-buttons">
              <button
                className="primary-action hyperopt-start-action"
                type="button"
                onClick={runOptimization}
                disabled={!scopeEnabled || !dateRangeValid || !costSettingsValid || !positionSizeValid || !hyperoptStopLossValid || !emaTrendTimeframeValid || quoteQuery.isPending || quoteQuery.isError || setupQuery.isPending || setupQuery.isError || researchBusy || !strategies.length}
              >
                {running || hyperoptMutation.isPending ? '⏳ Optimizing…' : '🚀 Start Hyperopt'}
              </button>
              <button
                className="secondary-action hyperopt-stop-action"
                type="button"
                onClick={() => stopMutation.mutate()}
                disabled={!running || stopMutation.isPending}
              >
                {stopMutation.isPending ? '⏳ Stopping…' : '🛑 Stop Hyperopt'}
              </button>
            </div>
          </div>
        </div>

        <div className="panel research-panel hyperopt-results-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">📊 Results · 03</p>
              <h3>Hyperopt results &amp; validation</h3>
            </div>
            <span className="pill neutral">{historyReports.length} / 10 results</span>
          </div>
          <div className="hyperopt-result-highlights">
            <div className="hyperopt-result-highlight approved">
              <span>✅ Last approved</span>
              <strong>
                {latestApprovedReport
                  ? `${formatUtcDateTime(latestApprovedReport.completedAt)} · ${latestApprovedReport.report.dataRevision}`
                  : approvedRevision && approvedRevisionMatchesScope
                    ? `${formatUtcDateTime(approvedRevision.approvedAt)} · ${approvedRevision.strategyClass}`
                    : 'No approved Hyperopt result'}
              </strong>
            </div>
            <div className="hyperopt-result-highlight pending">
              <span>🕒 Latest completed, not approved</span>
              <strong>
                {latestUnapprovedReport
                  ? `${formatUtcDateTime(latestUnapprovedReport.completedAt)} · ${latestUnapprovedReport.report.strategyClass ?? latestUnapprovedReport.report.strategy}`
                  : 'No result waiting for approval'}
              </strong>
            </div>
          </div>
          {reportsQuery.isPending && <p>Loading recent Hyperopt results…</p>}
          {reportsQuery.error && <p role="alert">Hyperopt history unavailable: {reportsQuery.error.message}</p>}
          {!reportsQuery.isPending && historyReports.length === 0 && (
            <p>No Hyperopt results exist for this pair, timeframe, and strategy yet.</p>
          )}
          <div className="hyperopt-result-list">
            {historyReports.map(({ completedAt, report: result }, index) => {
              const isApproved = Boolean(
                result.dataRevision && result.dataRevision === approvedDataRevision,
              )
              const isLatestUnapproved = result.dataRevision
                === latestUnapprovedReport?.report.dataRevision
              return (
                <article
                  className={`hyperopt-result-card${index === 0 ? ' newest' : ''}${isApproved ? ' approved' : ''}`}
                  key={result.dataRevision || completedAt}
                >
                  <div className="hyperopt-result-card-heading">
                    <div>
                      <div className="hyperopt-result-badges">
                        {index === 0 && <span className="hyperopt-result-badge newest">✨ Newest</span>}
                        {isApproved && <span className="hyperopt-result-badge approved">✅ Approved</span>}
                        {isLatestUnapproved && <span className="hyperopt-result-badge pending">🕒 Waiting approval</span>}
                        {result.status !== 'completed' && (
                          <span className="hyperopt-result-badge failed">⚠️ {result.status}</span>
                        )}
                      </div>
                      <h4>{result.strategyClass ?? result.strategy} · {result.pair} · {result.timeframe}</h4>
                      <p>🗓️ {formatUtcDateTime(completedAt)} · {result.candidatesTested} candidates · {result.steps} candles</p>
                    </div>
                    {result.status === 'completed' && (
                      <button
                        className={isApproved ? 'secondary-action' : 'primary-action'}
                        type="button"
                        onClick={() => saveReview(result.dataRevision)}
                        disabled={isApproved || !result.dataRevision || reviewMutation.isPending}
                      >
                        {reviewMutation.isPending
                          ? '⏳ Saving…'
                          : isApproved
                            ? '✅ Active approved result'
                            : '✅ Approve this result'}
                      </button>
                    )}
                  </div>
                  <div className="hyperopt-result-metrics">
                    <span>Validation P/L <strong>{result.validation.netPl}</strong></span>
                    <span>Return <strong className={metricTone(result.validation.profitPercent, 'return')}>{formatPercent(result.validation.profitPercent, true)}</strong></span>
                    <span>Win rate <strong className={metricTone(result.validation.winRate, 'win-rate', result.validation.trades)}>{formatPercent(result.validation.winRate)}</strong></span>
                    <span>Drawdown <strong>{result.validation.drawdown}</strong></span>
                  </div>
                  <details className="hyperopt-result-details">
                    <summary>🔍 Expand metrics and optimized parameters</summary>
                    <div className="bullet-list">
                      <div><span>Best objective</span><strong>{result.objective}</strong></div>
                      <div><span>Best parameters</span><strong>{Object.entries(result.bestParameters).map(([key, value]) => `${key}: ${value}`).join(' · ') || 'No tunable parameters'}</strong></div>
                      {result.bestMinimalRoi && Object.keys(result.bestMinimalRoi).length > 0 && (
                        <div><span>Minimal ROI</span><strong>{Object.entries(result.bestMinimalRoi).map(([key, value]) => `${key}m: ${value}`).join(' · ')}</strong></div>
                      )}
                      {result.stopLoss && (
                        <div><span>Stop loss</span><strong>{result.stopLoss.value} {result.stopLoss.unit} · {result.stopLoss.optimized ? 'optimized' : 'static'}</strong></div>
                      )}
                      <div><span>Trailing stop</span><strong>{result.trailingStopLoss ? 'Enabled' : 'Disabled'}</strong></div>
                      <div><span>ROI / exit signals</span><strong>ROI {result.optimizeRoi === false ? 'disabled' : 'enabled'} · exit signals {result.useExitSignal === false ? 'disabled' : 'enabled'}</strong></div>
                      <div><span>Best parameters by candidate</span><strong>{result.candidates.slice(0, 3).map((candidate) => `#${candidate.rank}: ${Object.entries(candidate.parameters).map(([key, value]) => `${key}=${value}`).join(', ')}`).join(' · ') || 'No candidate details'}</strong></div>
                    </div>
                  </details>
                </article>
              )
            })}
          </div>
          {reviewQuery.error && <p role="alert">Approval status unavailable: {reviewQuery.error.message}</p>}
          {reviewMutation.error && <p role="alert">Could not approve Hyperopt result: {reviewMutation.error.message}</p>}
        </div>
      </section>
    </>
  )
}
