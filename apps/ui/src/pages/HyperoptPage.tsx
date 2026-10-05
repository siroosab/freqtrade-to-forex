import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
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

export function HyperoptPage() {
  const pairs = useUiStore((state) => state.selectedInstruments)
  const [pair, setPair] = useState(pairs[0] ?? 'EUR/USD')
  const [timeframe, setTimeframe] = useState('M15')
  const [strategyClass, setStrategyClass] = useState('')
  const [historyValue, setHistoryValue] = useState(500)
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

  const report = statusQuery.data?.report ?? reportQuery.data?.report
  const backtest = backtestQuery.data?.result ?? backtestQuery.data
  const running = statusQuery.data?.status === 'running'
  const backtestRunning = Boolean(
    backtestJobId
    && backtestQuery.data?.status !== 'completed'
    && backtestQuery.data?.status !== 'failed',
  )
  const researchBusy = running || backtestRunning || hyperoptMutation.isPending || backtestMutation.isPending
  const strategies = strategiesQuery.data ?? []

  const runResearch = () => {
    const history = { historyMode: 'candles' as const, historyValue, steps: historyValue }
    backtestMutation.mutate({ pair, timeframe, strategyClass, ...history })
  }

  const runOptimization = () => {
    hyperoptMutation.mutate({
      pair,
      timeframe,
      strategyClass,
      steps: historyValue,
      historyMode: 'candles',
      historyValue,
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
              <span>History (candles)</span>
              <input type="number" min="40" max="10000" value={historyValue} onChange={(event) => setHistoryValue(Number(event.target.value))} />
            </label>
          </div>
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
            <button className="primary-action" type="button" onClick={runResearch} disabled={!scopeEnabled || researchBusy}>
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
              <button className="primary-action" type="button" onClick={runOptimization} disabled={!scopeEnabled || researchBusy || !strategies.length}>
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
                  <tbody>{report.candidates.slice(0, 10).map((candidate) => (
                    <tr key={candidate.rank}>
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
