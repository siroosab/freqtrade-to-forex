import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  clearCandleCache,
  downloadCandleDateRange,
  getCandleCacheInventory,
  getAvailableStrategies,
  getBacktestJob,
  getHyperoptLossFunctions,
  getHyperoptReport,
  getHyperoptStatus,
  getStrategyReview,
  runBacktest,
  saveStrategyReview,
  startHyperopt,
  stopHyperopt,
} from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H2', 'H4', 'H6', 'H8', 'H12', 'D1', 'W1', 'MN1']

function initialDateRange(): { startDate: string; endDate: string } {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - 29)
  const localDate = (value: Date) => (
    `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
  )
  return {
    startDate: localDate(start),
    endDate: localDate(end),
  }
}

function formatUtcDateTime(value: string): string {
  return `${new Date(value).toLocaleString(undefined, { timeZone: 'UTC' })} UTC`
}

export function HyperoptPage() {
  const queryClient = useQueryClient()
  const pairs = useUiStore((state) => state.selectedInstruments)
  const [pair, setPair] = useState(pairs[0] ?? 'EUR/USD')
  const [timeframe, setTimeframe] = useState('M15')
  const [strategyClass, setStrategyClass] = useState('')
  const [historyValue, setHistoryValue] = useState(500)
  const [historyMode, setHistoryMode] = useState<'candles' | 'days' | 'date_range'>('candles')
  const [dateRange, setDateRange] = useState(initialDateRange)
  const [attempts, setAttempts] = useState(30)
  const [lossFunction, setLossFunction] = useState('ProfitDrawDownHyperOptLoss')
  const [backtestJobId, setBacktestJobId] = useState<string | null>(null)
  const strategiesQuery = useQuery({
    queryKey: ['available-strategies'],
    queryFn: getAvailableStrategies,
  })
  const lossFunctionsQuery = useQuery({
    queryKey: ['hyperopt-loss-functions'],
    queryFn: getHyperoptLossFunctions,
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
  const reviewQuery = useQuery({
    queryKey: ['strategy-review', pair, timeframe, strategyClass],
    queryFn: () => getStrategyReview(pair, timeframe, strategyClass),
    enabled: scopeEnabled,
  })
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
      void reviewQuery.refetch()
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
    onSuccess: () => void reviewQuery.refetch(),
  })
  const downloadMutation = useMutation({
    mutationFn: downloadCandleDateRange,
    onSuccess: async () => {
      setHistoryMode('date_range')
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
  const backtest = backtestQuery.data?.result ?? backtestQuery.data
  const running = statusQuery.data?.status === 'running'
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
  const strategies = strategiesQuery.data ?? []
  const dateRangeValid = Boolean(dateRange.startDate && dateRange.endDate)
    && dateRange.startDate <= dateRange.endDate
  const historyValueValid = historyMode === 'date_range'
    ? dateRangeValid
    : Number.isInteger(historyValue)
      && historyValue >= (historyMode === 'candles' ? 40 : 1)
      && historyValue <= 10000

  useEffect(() => {
    if (backtestQuery.data?.status === 'completed') {
      void queryClient.invalidateQueries({ queryKey: ['candle-cache', pair, timeframe] })
    }
  }, [backtestQuery.data?.status, pair, queryClient, timeframe])

  const runResearch = () => {
    const history = {
      historyMode,
      historyValue,
      steps: historyValue,
      ...(historyMode === 'date_range' ? dateRange : {}),
    }
    backtestMutation.mutate({ pair, timeframe, strategyClass, ...history })
  }

  const runOptimization = () => {
    hyperoptMutation.mutate({
      pair,
      timeframe,
      strategyClass,
      steps: historyValue,
      historyMode,
      historyValue,
      ...(historyMode === 'date_range' ? dateRange : {}),
      attempts,
      hyperoptLoss: lossFunction,
    })
  }

  const saveReview = (status: 'approved' | 'rejected') => {
    reviewMutation.mutate({
      status,
      pair,
      timeframe,
      strategyClass,
      requireOptimization: true,
      notes: status === 'approved'
        ? 'Hyperopt result approved for the selected pair, timeframe, and strategy.'
        : 'Hyperopt result rejected; it must not be used for signal generation.',
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

      <section className="content-grid">
        <div className="panel">
          <div className="panel-header compact">
            <div><p className="eyebrow">Scope</p><h3>Choose research inputs</h3></div>
          </div>
          <div className="settings-grid">
            <label className="field-block">
              <span>Pair</span>
              <select value={pair} onChange={(event) => setPair(event.target.value)}>
                {(pairs.length ? pairs : ['EUR/USD']).map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <label className="field-block">
              <span>Timeframe</span>
              <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
                {timeframeOptions.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <label className="field-block">
              <span>Strategy</span>
              <select value={strategyClass} onChange={(event) => setStrategyClass(event.target.value)}>
                {strategies.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
              </select>
            </label>
            <label className="field-block">
              <span>History unit</span>
              <select
                value={historyMode}
                onChange={(event) => {
                  const mode = event.target.value as 'candles' | 'days' | 'date_range'
                  setHistoryMode(mode)
                  if (mode !== 'date_range') setHistoryValue(mode === 'days' ? 30 : 500)
                }}
              >
                <option value="candles">Candles</option>
                <option value="days">Days</option>
                <option value="date_range">Date range</option>
              </select>
            </label>
            <label className="field-block">
              <span>{historyMode === 'date_range' ? 'Hyperopt input' : `History (${historyMode})`}</span>
              {historyMode === 'date_range' ? (
                <strong>{dateRange.startDate} through {dateRange.endDate}</strong>
              ) : (
                <input
                  type="number"
                  min={historyMode === 'candles' ? 40 : 1}
                  max="10000"
                  value={historyValue}
                  onChange={(event) => setHistoryValue(Number(event.target.value))}
                />
              )}
            </label>
          </div>
          <div className="settings-grid">
            <label className="field-block">
              <span>Download from (UTC)</span>
              <input
                type="date"
                value={dateRange.startDate}
                max={dateRange.endDate || undefined}
                onChange={(event) => setDateRange((current) => ({ ...current, startDate: event.target.value }))}
              />
            </label>
            <label className="field-block">
              <span>Download through (UTC)</span>
              <input
                type="date"
                value={dateRange.endDate}
                min={dateRange.startDate || undefined}
                onChange={(event) => setDateRange((current) => ({ ...current, endDate: event.target.value }))}
              />
            </label>
            <div className="field-block">
              <span>Cached {timeframe} candles for {pair}</span>
              {cacheQuery.isLoading ? (
                <strong>Loading cache inventory…</strong>
              ) : cacheQuery.data ? (
                <strong>
                  {cacheQuery.data.candles.toLocaleString()} candles across {cacheQuery.data.cachedRanges} cached range(s)
                  {cacheQuery.data.from && cacheQuery.data.to
                    ? ` · ${formatUtcDateTime(cacheQuery.data.from)} – ${formatUtcDateTime(cacheQuery.data.to)}`
                    : ''}
                </strong>
              ) : null}
            </div>
            <div className="summary-grid">
              <button
                className="primary-action"
                type="button"
                onClick={() => downloadMutation.mutate({ pair, timeframe, ...dateRange })}
                disabled={!scopeEnabled || !dateRangeValid || researchBusy}
              >
                {downloadMutation.isPending ? 'Downloading…' : 'Download date range'}
              </button>
              <button
                className="secondary-action"
                type="button"
                onClick={clearSelectedCache}
                disabled={!cacheQuery.data?.cachedRanges || researchBusy}
              >
                {clearCacheMutation.isPending ? 'Clearing…' : 'Clear this pair/timeframe'}
              </button>
            </div>
          </div>
          {cacheQuery.data?.ranges.map((range) => (
            <p className="muted" key={range.key}>
              {range.kind === 'range' ? 'Date range' : 'Recent cache'} · {range.candles.toLocaleString()} candles
              {range.from && range.to
                ? ` · ${formatUtcDateTime(range.from)} – ${formatUtcDateTime(range.to)}`
                : ''}
            </p>
          ))}
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
        </div>

        <div className="panel research-panel">
          <div className="panel-header compact">
            <div><p className="eyebrow">Research · 01</p><h3>Backtest</h3></div>
            <button className="primary-action" type="button" onClick={runResearch} disabled={!scopeEnabled || !historyValueValid || researchBusy}>
              {backtestMutation.isPending || backtestRunning ? 'Backtest running…' : 'Run backtest'}
            </button>
          </div>
          <p>{pair} · {timeframe} · {strategyClass || 'Select a strategy'}</p>
          {backtest && (
            <div className="bullet-list">
              <div><span>Status</span><strong>{backtest.status}</strong></div>
              <div><span>Net P/L</span><strong>{backtest.netPl ?? '—'}</strong></div>
              <div><span>Trades</span><strong>{backtest.trades ?? '—'}</strong></div>
              <div><span>Max drawdown</span><strong>{backtest.maxDrawdown ?? '—'}</strong></div>
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

        <div className="panel research-panel">
          <div className="panel-header compact">
            <div><p className="eyebrow">02 · Manual Hyperopt</p><h3>Optimize strategy parameters</h3></div>
            <div className="summary-grid">
              <button className="primary-action" type="button" onClick={runOptimization} disabled={!scopeEnabled || !historyValueValid || researchBusy || !strategies.length}>
                {running || hyperoptMutation.isPending ? 'Optimizing…' : 'Run hyperopt'}
              </button>
              <button className="secondary-action" type="button" onClick={() => stopMutation.mutate()} disabled={!running || stopMutation.isPending}>
                Stop
              </button>
            </div>
          </div>
          <div className="settings-grid">
            <label className="field-block">
              <span>Attempts</span>
              <input type="number" min="1" max="900" value={attempts} onChange={(event) => setAttempts(Number(event.target.value))} />
            </label>
            <label className="field-block">
              <span>Objective</span>
              <select value={lossFunction} onChange={(event) => setLossFunction(event.target.value)}>
                {(lossFunctionsQuery.data?.options ?? [lossFunction]).map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
          </div>
          {running && statusQuery.data && (
            <p aria-live="polite">Progress: {statusQuery.data.attemptsCompleted} / {statusQuery.data.attemptsTotal} attempts</p>
          )}
          {statusQuery.data?.error && <p role="alert">Hyperopt failed: {statusQuery.data.error}</p>}
          {hyperoptMutation.error && <p role="alert">Hyperopt request failed: {hyperoptMutation.error.message}</p>}
          {statusQuery.error && <p role="alert">Hyperopt status failed: {statusQuery.error.message}</p>}
          {stopMutation.error && <p role="alert">Stop request failed: {stopMutation.error.message}</p>}
          {report && (
            <>
              <div className="bullet-list">
                <div><span>Data coverage</span><strong>{report.steps} candles · train {report.trainCandles} / validate {report.validationCandles}</strong></div>
                <div><span>Validation result</span><strong>{report.validation.netPl} P/L · {report.validation.trades} trades · drawdown {report.validation.drawdown}</strong></div>
                <div><span>Best objective</span><strong>{report.objective}</strong></div>
                <div><span>Best parameters</span><strong>{Object.entries(report.bestParameters).map(([key, value]) => `${key}: ${value}`).join(' · ') || 'No tunable parameters'}</strong></div>
                {report.bestMinimalRoi && <div><span>Minimal ROI</span><strong>{Object.entries(report.bestMinimalRoi).map(([key, value]) => `${key}m: ${value}`).join(' · ')}</strong></div>}
              </div>
              <div className="strategy-table-wrap">
                <table className="positions-table">
                  <thead><tr><th>Rank</th><th>Objective</th><th>Validation P/L</th><th>Drawdown</th><th>Trades</th><th>Parameters</th></tr></thead>
                  <tbody>{report.candidates.slice(-3).map((candidate) => (
                    <tr
                      key={candidate.rank}
                      className={Number(candidate.validationNetPl) > 0 ? 'row-profit' : Number(candidate.validationNetPl) < 0 ? 'row-loss' : undefined}
                    >
                      <td>{candidate.rank}</td>
                      <td>{candidate.objective}</td>
                      <td>{candidate.validationNetPl}</td>
                      <td>{candidate.validationDrawdown}</td>
                      <td>{candidate.validationTrades}</td>
                      <td>{Object.entries(candidate.parameters).map(([key, value]) => `${key}: ${value}`).join(' · ')}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </>
          )}
          {!report && reportQuery.data?.available === false && <p>No Hyperopt result exists for this pair, timeframe, and strategy yet.</p>}
          {reportQuery.error && <p role="alert">Hyperopt report unavailable: {reportQuery.error.message}</p>}
        </div>

        <div className="panel">
          <div className="panel-header compact">
            <div><p className="eyebrow">03 · User decision</p><h3>Approve optimized strategy</h3></div>
            <span className={`pill ${reviewQuery.data?.status === 'approved' ? 'positive' : 'neutral'}`}>
              {reviewQuery.data?.status ?? 'pending'}
            </span>
          </div>
          <p>Approval binds the optimized parameters to this exact pair, timeframe, and strategy. Only an approved revision is used by automatic signal generation and order execution.</p>
          {reviewQuery.data?.approvedRevision && (
            <p>Active revision: {reviewQuery.data.approvedRevision.pair} · {reviewQuery.data.approvedRevision.timeframe} · {reviewQuery.data.approvedRevision.strategyClass}</p>
          )}
          <div className="summary-grid">
            <button className="primary-action" type="button" onClick={() => saveReview('approved')} disabled={!report || report.status === 'failed' || reviewMutation.isPending || running}>
              {reviewMutation.isPending ? 'Saving approval…' : 'Approve hyperopt result'}
            </button>
            <button className="secondary-action" type="button" onClick={() => saveReview('rejected')} disabled={!report || reviewMutation.isPending}>
              Reject result
            </button>
          </div>
          {reviewQuery.error && <p role="alert">Approval status unavailable: {reviewQuery.error.message}</p>}
          {reviewMutation.error && <p role="alert">Approval failed: {reviewMutation.error.message}</p>}
          {reviewMutation.isSuccess && <p aria-live="polite">Decision saved. The approved strategy configuration is now available to the signal and execution worker.</p>}
        </div>
      </section>
    </>
  )
}
