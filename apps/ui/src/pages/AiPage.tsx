import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { getAiConfig, getAiHyperoptLossFunctions, getAiHyperoptReport, getAiHyperoptScheduler, getAiHyperoptStatus, getAiModelComparison, getAiReview, getAiSignals, getAiStatus, runAiHyperoptSchedulerNow, saveAiConfig, saveAiHyperoptScheduler, saveAiReview, startAiHyperopt, stopAiHyperopt, type AiConfig } from '../api/mockApi'

const FEATURE_OPTIONS = ['trend', 'spread', 'session', 'volatility']

export function AiPage() {
  const [selectedPair, setSelectedPair] = useState('EUR/USD')
  const [selectedTimeframe, setSelectedTimeframe] = useState<AiConfig['timeframe']>('M5')
  const [historyMode, setHistoryMode] = useState<'candles' | 'days'>('candles')
  const [historyValue, setHistoryValue] = useState(250)
  const [attempts, setAttempts] = useState(24)
  const [hyperoptLoss, setHyperoptLoss] = useState('ProfitDrawDownHyperOptLoss')
  const [schedulerPairs, setSchedulerPairs] = useState<string[]>(['EUR/USD', 'GBP/USD'])
  const [schedulerIntervalDays, setSchedulerIntervalDays] = useState(2)
  const [schedulerGapMinutes, setSchedulerGapMinutes] = useState(120)
  const [featureSet, setFeatureSet] = useState<string[]>(FEATURE_OPTIONS)
  const [freqaiForm, setFreqaiForm] = useState({
    trainPeriodDays: '30',
    backtestPeriodDays: '7',
    labelPeriodCandles: '2',
    includeShiftedCandles: '0',
    indicatorPeriodsCandles: '5,14',
    weightFactor: '0',
    diThreshold: '0',
  })
  const aiConfigQuery = useQuery({ queryKey: ['ai-config', selectedPair], queryFn: () => getAiConfig(selectedPair) })
  const reviewQuery = useQuery({ queryKey: ['ai-review', selectedPair, selectedTimeframe], queryFn: () => getAiReview(selectedPair, selectedTimeframe) })
  const statusQuery = useQuery({ queryKey: ['ai-status', selectedPair], queryFn: () => getAiStatus(selectedPair), refetchInterval: 5000 })
  const signalsQuery = useQuery({ queryKey: ['ai-signals', selectedPair, selectedTimeframe], queryFn: () => getAiSignals(selectedPair, selectedTimeframe), refetchInterval: 30000 })
  const comparisonQuery = useQuery({ queryKey: ['ai-comparison', selectedPair, selectedTimeframe], queryFn: () => getAiModelComparison(selectedPair, selectedTimeframe), refetchInterval: 60000 })
  const hyperoptLossFunctionsQuery = useQuery({ queryKey: ['ai-hyperopt-loss-functions'], queryFn: getAiHyperoptLossFunctions, staleTime: Infinity })
  const schedulerQuery = useQuery({ queryKey: ['ai-hyperopt-scheduler'], queryFn: getAiHyperoptScheduler, refetchInterval: 10000 })
  const hyperoptStatusQuery = useQuery({
    queryKey: ['ai-hyperopt-status', selectedPair],
    queryFn: () => getAiHyperoptStatus(selectedPair),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1000 : false),
  })
  const hyperoptReportQuery = useQuery({ queryKey: ['ai-hyperopt-report', selectedPair], queryFn: () => getAiHyperoptReport(selectedPair) })
  const startHyperoptMutation = useMutation({
    mutationFn: startAiHyperopt,
    onSuccess: () => { void hyperoptStatusQuery.refetch() },
  })
  const stopHyperoptMutation = useMutation({
    mutationFn: () => stopAiHyperopt(selectedPair),
    onSuccess: () => { void hyperoptStatusQuery.refetch() },
  })
  const schedulerMutation = useMutation({ mutationFn: saveAiHyperoptScheduler, onSuccess: (data) => { setSchedulerPairs(data.pairs); setSchedulerIntervalDays(data.intervalDays); setSchedulerGapMinutes(data.gapMinutes); void schedulerQuery.refetch() } })
  const schedulerRunMutation = useMutation({ mutationFn: runAiHyperoptSchedulerNow, onSuccess: () => { void schedulerQuery.refetch() } })
  const isHyperoptRunning = hyperoptStatusQuery.data?.status === 'running'
  useEffect(() => {
    if (!schedulerQuery.data) return
    setSchedulerPairs(schedulerQuery.data.pairs.length ? schedulerQuery.data.pairs : schedulerQuery.data.approvedPairs.slice(0, 2))
    setSchedulerIntervalDays(schedulerQuery.data.intervalDays)
    setSchedulerGapMinutes(schedulerQuery.data.gapMinutes)
  }, [schedulerQuery.data])
  useEffect(() => {
    if (hyperoptStatusQuery.data?.status === 'completed' || hyperoptStatusQuery.data?.status === 'stopped') {
      void hyperoptReportQuery.refetch()
      void statusQuery.refetch()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hyperoptStatusQuery.data?.status])
  const timeframeMutation = useMutation({ mutationFn: (config: Parameters<typeof saveAiConfig>[0]) => saveAiConfig(config, selectedPair), onSuccess: () => { void aiConfigQuery.refetch(); void statusQuery.refetch() } })
  const freqaiMutation = useMutation({
    mutationFn: (config: Parameters<typeof saveAiConfig>[0]) => saveAiConfig(config, selectedPair),
    onSuccess: () => { void aiConfigQuery.refetch(); void comparisonQuery.refetch() },
  })
  const reviewMutation = useMutation({
    mutationFn: saveAiReview,
    onSuccess: () => {
      void reviewQuery.refetch()
    },
  })

  const handleReview = (status: 'approved' | 'rejected') => {
    reviewMutation.mutate({
      status,
      pair: selectedPair,
      timeframe: selectedTimeframe,
      requireOptimization: true,
      notes:
        status === 'approved'
          ? 'Approved for Practice-safe dry-run evaluation only.'
          : 'Rejected: requires additional validation before Practice-safe execution.',
      guardrails: reviewQuery.data?.guardrails ?? [],
    })
  }

  useEffect(() => {
    if (aiConfigQuery.data?.timeframe) setSelectedTimeframe(aiConfigQuery.data.timeframe)
  }, [aiConfigQuery.data?.timeframe])

  useEffect(() => {
    if (hyperoptLossFunctionsQuery.data?.default) setHyperoptLoss(hyperoptLossFunctionsQuery.data.default)
  }, [hyperoptLossFunctionsQuery.data?.default])

  useEffect(() => {
    if (!aiConfigQuery.data) return
    setFeatureSet(aiConfigQuery.data.featureSet)
    const freqai = aiConfigQuery.data.freqai
    if (freqai) {
      setFreqaiForm({
        trainPeriodDays: String(freqai.trainPeriodDays),
        backtestPeriodDays: String(freqai.backtestPeriodDays),
        labelPeriodCandles: String(freqai.featureParameters.labelPeriodCandles),
        includeShiftedCandles: String(freqai.featureParameters.includeShiftedCandles),
        indicatorPeriodsCandles: freqai.featureParameters.indicatorPeriodsCandles.join(','),
        weightFactor: String(freqai.featureParameters.weightFactor),
        diThreshold: String(freqai.featureParameters.diThreshold),
      })
    }
  }, [aiConfigQuery.data])

  const toggleFeature = (feature: string) => setFeatureSet((current) => (current.includes(feature) ? current.filter((item) => item !== feature) : [...current, feature]))

  const handleHyperopt = () => startHyperoptMutation.mutate({ pair: selectedPair, timeframe: selectedTimeframe, steps: historyMode === 'candles' ? historyValue : 250, historyMode, historyValue, attempts, hyperoptLoss })
  const handleStopHyperopt = () => stopHyperoptMutation.mutate()
  const toggleSchedulerPair = (pair: string) => setSchedulerPairs((current) => current.includes(pair) ? current.filter((item) => item !== pair) : [...current, pair])
  const handleTimeframeApply = () => {
    if (!aiConfigQuery.data) return
    timeframeMutation.mutate({ ...aiConfigQuery.data, timeframe: selectedTimeframe })
  }
  const handleFreqaiSave = () => {
    if (!aiConfigQuery.data) return
    const indicatorPeriodsCandles = freqaiForm.indicatorPeriodsCandles
      .split(',')
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isFinite(value) && value >= 2)
    freqaiMutation.mutate({
      ...aiConfigQuery.data,
      featureSet,
      freqai: {
        trainPeriodDays: Number(freqaiForm.trainPeriodDays) || 30,
        backtestPeriodDays: Number(freqaiForm.backtestPeriodDays) || 7,
        featureParameters: {
          labelPeriodCandles: Number(freqaiForm.labelPeriodCandles) || 2,
          includeShiftedCandles: Number(freqaiForm.includeShiftedCandles) || 0,
          indicatorPeriodsCandles: indicatorPeriodsCandles.length ? indicatorPeriodsCandles : [5, 14],
          weightFactor: Number(freqaiForm.weightFactor) || 0,
          diThreshold: Number(freqaiForm.diThreshold) || 0,
        },
      },
    })
  }

  const effectiveReview = reviewQuery.data ?? {
    status: 'pending' as const,
    strategyName: 'FX Trend Pulse',
    model: 'hybrid',
    riskPolicy: 'Practice-safe',
    guardrails: ['dry-run only', 'no live order execution', 'manual approval required'],
    notes: 'Approval data is still loading.',
    lastUpdated: new Date().toISOString(),
  }

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">AI</p>
          <h2>Strategy intelligence</h2>
        </div>
        <span className="pill neutral">Practice-safe</span>
      </header>

      {(aiConfigQuery.isLoading || statusQuery.isLoading) && !aiConfigQuery.data && !statusQuery.data && <div className="panel page-panel"><p>Loading AI strategy evidence…</p></div>}
      {(aiConfigQuery.isError || reviewQuery.isError || statusQuery.isError) && <div className="panel page-panel"><p>AI configuration endpoint unavailable; fallback config is being used.</p></div>}

      {aiConfigQuery.data && statusQuery.data && (
        <section className="content-grid ai-page-grid">
          <div className="ai-panel-parent ai-panel-parent-left">
          <div className="panel">
            <div className="panel-header compact"><div><p className="eyebrow">Runtime evidence</p><h3>What is actually running</h3></div><span className="pill neutral">{statusQuery.data.state}</span></div>
            <div className="bullet-list">
              <div><span>Strategy contract</span><strong>{statusQuery.data.strategy}</strong></div>
              <div><span>Version / config</span><strong>{statusQuery.data.strategyVersion} / {statusQuery.data.configRevision}</strong></div>
              <div><span>Model / feature schema</span><strong>{statusQuery.data.modelVersion ?? statusQuery.data.strategyVersion} / {statusQuery.data.featureSchemaHash ?? 'not available'}</strong></div>
              <div><span>Execution</span><strong>{statusQuery.data.executionMode} • live disabled</strong></div>
              <div><span>Evidence</span><strong>{statusQuery.data.evidence.validation}</strong></div>
              <div><span>Independent backtest</span><strong>{statusQuery.data.evidence.independentBacktest}</strong></div>
              <div><span>Last backtest</span><strong>{statusQuery.data.lastBacktest ? `${statusQuery.data.lastBacktest.result ?? 'completed'} • ${statusQuery.data.lastBacktest.trades ?? 0} trades` : 'Not run yet'}</strong></div>
              <div><span>Training data hash</span><strong>{statusQuery.data.trainingDataHash ?? 'not available'}</strong></div>
              <div><span>Last parameter improvement</span><strong>{statusQuery.data.lastOptimizationAttempt ? new Date(statusQuery.data.lastOptimizationAttempt).toLocaleString() : 'Not run yet'}</strong></div>
            </div>
          </div>

          <div className="panel"><div className="panel-header compact"><div><p className="eyebrow">Research comparison</p><h3>Models on the same OOS split</h3></div><span className="pill neutral">research-only</span></div>{comparisonQuery.isLoading && <p>Loading model comparison…</p>}{comparisonQuery.isError && <p>Model comparison unavailable from broker data.</p>}{comparisonQuery.data && <div className="bullet-list"><div><span>Dataset / split</span><strong>{comparisonQuery.data.comparison.dataset.pair} • {comparisonQuery.data.comparison.dataset.timeframe} • train {comparisonQuery.data.comparison.dataset.trainRows} / validation {comparisonQuery.data.comparison.dataset.validationRows} / OOS {comparisonQuery.data.comparison.dataset.oosRows}</strong></div><div><span>Regressor OOS MAE / RMSE</span><strong>{comparisonQuery.data.comparison.regressor.oosMae} / {comparisonQuery.data.comparison.regressor.oosRmse}</strong></div><div><span>Regressor direction accuracy</span><strong>{comparisonQuery.data.comparison.regressor.directionalAccuracy}</strong></div><div><span>Classifier accuracy / Macro-F1</span><strong>{comparisonQuery.data.comparison.classifier.accuracy.toFixed(3)} / {comparisonQuery.data.comparison.classifier.f1Macro.toFixed(3)}</strong></div><div><span>Model acceptance</span><strong>Regressor: <span className={comparisonQuery.data.comparison.regressor.accepted ? 'pill positive' : 'pill negative'}>{comparisonQuery.data.comparison.regressor.accepted ? 'accepted' : 'rejected'}</span> • Classifier: <span className={comparisonQuery.data.comparison.classifier.accepted ? 'pill positive' : 'pill negative'}>{comparisonQuery.data.comparison.classifier.accepted ? 'accepted' : 'rejected'}</span></strong></div><div><span>Rejection reasons</span><strong>{[...comparisonQuery.data.comparison.regressor.rejectionReasons, ...comparisonQuery.data.comparison.classifier.rejectionReasons].join(' • ') || 'None'}</strong></div><div><span>Baseline OOS signals</span><strong>{comparisonQuery.data.comparison.baseline.nonFlatSignals} / {comparisonQuery.data.comparison.baseline.oosSamples}</strong></div><div><span>Feature engineering</span><strong>indicator periods {comparisonQuery.data.comparison.dataset.indicatorPeriods.join(',')} • shifted candles {comparisonQuery.data.comparison.dataset.includeShiftedCandles} • train/backtest days {comparisonQuery.data.comparison.dataset.trainPeriodDays ?? '-'}/{comparisonQuery.data.comparison.dataset.backtestPeriodDays ?? '-'}</strong></div><div><span>Weight factor / DI threshold</span><strong>{comparisonQuery.data.comparison.dataset.weightFactor} / {comparisonQuery.data.comparison.dataset.diThreshold}</strong></div><div><span>DI filtered OOS rows</span><strong>Regressor: {comparisonQuery.data.comparison.regressor.diFilteredOosRows} • Classifier: {comparisonQuery.data.comparison.classifier.diFilteredOosRows}</strong></div><div><span>Data / feature hash</span><strong>{comparisonQuery.data.comparison.dataHash} / {comparisonQuery.data.comparison.featureSchemaHash}</strong></div></div>}</div>

          <div className="panel">
            <div className="panel-header compact"><div><p className="eyebrow">Explainability</p><h3>Signal trace</h3></div><span className="pill neutral">{signalsQuery.data?.configRevision ?? 'loading'}</span></div>
            {signalsQuery.isLoading && <p>Loading signal evidence…</p>}
            {signalsQuery.isError && <p>Signal trace unavailable from broker API.</p>}
            {signalsQuery.data && <div className="strategy-table-wrap ai-signal-trace-scroll"><table className="positions-table"><thead><tr><th>Time</th><th>Signal</th><th>Reason</th><th>Strength</th><th>Spread</th><th>Volatility</th><th>ATR</th><th>Session</th></tr></thead><tbody>{signalsQuery.data.signals.slice(-10).reverse().map((signal, index) => <tr key={`${signal.time ?? 'signal'}-${index}`} className={signal.signal === 'long' ? 'row-profit' : signal.signal === 'short' ? 'row-loss' : undefined}><td>{signal.time ? new Date(signal.time).toLocaleString() : '-'}</td><td className={signal.signal === 'long' ? 'long' : signal.signal === 'short' ? 'short' : undefined}>{signal.signal}</td><td>{signal.reason}</td><td>{signal.signalStrength?.toFixed(3) ?? '-'}</td><td>{signal.spreadPct?.toFixed(5) ?? '-'}</td><td>{signal.volatility?.toFixed(5) ?? '-'}</td><td>{signal.atr?.toFixed(5) ?? '-'}</td><td>{signal.sessionHour ?? '-'}</td></tr>)}</tbody></table></div>}
          </div>

          <div className="panel">
            <div className="panel-header compact"><div><p className="eyebrow">Automation</p><h3>Scheduled Hyperopt</h3></div><span className="pill neutral">sequential queue</span></div>
            <p>Runs one approved pair at a time. Each next pair receives the configured safety gap, so Hyperopt jobs never start simultaneously.</p>
            <div className="strategy-stack">
              {['EUR/USD', 'GBP/USD', 'USD/JPY'].map((pair) => { const approved = schedulerQuery.data?.approvedPairs.includes(pair) ?? false; return <label key={pair} className="strategy-card" style={{ cursor: approved ? 'pointer' : 'not-allowed', opacity: approved ? 1 : 0.55 }} title={approved ? 'Ready for scheduled Hyperopt' : 'Approve a Hyperopt revision for this pair first'}><div className="strategy-row"><strong>{pair}</strong><span>{approved ? <input type="checkbox" checked={schedulerPairs.includes(pair)} onChange={() => toggleSchedulerPair(pair)} /> : <span className="pill neutral">Approval required</span>}</span></div></label> })}
            </div>
            <div className="settings-grid" style={{ marginTop: '16px' }}><label className="field-block"><span>Repeat every (days)</span><input type="number" min="1" max="30" value={schedulerIntervalDays ?? 2} onChange={(event) => setSchedulerIntervalDays(Number(event.target.value) || 2)} /></label><label className="field-block"><span>Gap between pairs (minutes)</span><input type="number" min="1" max="1440" value={schedulerGapMinutes ?? 120} onChange={(event) => setSchedulerGapMinutes(Number(event.target.value) || 120)} /></label></div>
            <div className="summary-grid" style={{ marginTop: '14px' }}><button className="primary-action" onClick={() => schedulerMutation.mutate({ enabled: true, intervalDays: schedulerIntervalDays, gapMinutes: schedulerGapMinutes, pairs: schedulerPairs })} disabled={schedulerMutation.isPending || schedulerPairs.length === 0}>{schedulerMutation.isPending ? 'Saving…' : 'Enable automatic mode'}</button><button className="secondary-action" onClick={() => schedulerMutation.mutate({ enabled: false, intervalDays: schedulerIntervalDays, gapMinutes: schedulerGapMinutes, pairs: schedulerPairs })} disabled={schedulerMutation.isPending}>Disable</button><button className="secondary-action" onClick={() => schedulerRunMutation.mutate()} disabled={schedulerRunMutation.isPending || schedulerPairs.length === 0}>Run now</button></div>
            {schedulerQuery.data && <div className="bullet-list"><div><span>Status</span><strong>{schedulerQuery.data.enabled ? 'enabled' : 'disabled'}{schedulerQuery.data.running ? ' • running' : ''}</strong></div><div><span>Approved pairs</span><strong>{schedulerQuery.data.approvedPairs.join(' • ') || 'none'}</strong></div>{Object.entries(schedulerQuery.data.nextRuns ?? {}).map(([pair, scheduledAt]) => <div key={pair}><span>Next {pair}</span><strong>{new Date(scheduledAt).toLocaleString()}</strong></div>)}</div>}
            {schedulerMutation.error && <p>Scheduler update failed: {schedulerMutation.error.message}</p>}
            {schedulerRunMutation.error && <p>Scheduler run failed: {schedulerRunMutation.error.message}</p>}
          </div>

          <div className="panel">
            <div className="panel-header compact"><div><p className="eyebrow">Optimization</p><h3>AI hyperopt</h3></div><div style={{ display: 'flex', gap: '8px' }}><button className="primary-action" onClick={handleHyperopt} disabled={startHyperoptMutation.isPending || isHyperoptRunning}>{isHyperoptRunning ? 'Running…' : 'Run hyperopt'}</button><button className="secondary-action" onClick={handleStopHyperopt} disabled={!isHyperoptRunning || stopHyperoptMutation.isPending}>{stopHyperoptMutation.isPending ? 'Stopping…' : 'Stop'}</button></div></div>
            <div className="settings-grid"><label className="field-block"><span>Pair</span><select value={selectedPair} onChange={(event) => setSelectedPair(event.target.value)}><option>EUR/USD</option><option>GBP/USD</option><option>USD/JPY</option></select></label><label className="field-block"><span>Strategy timeframe</span><select value={selectedTimeframe} onChange={(event) => setSelectedTimeframe(event.target.value as AiConfig['timeframe'])}><option>M5</option><option>M15</option><option>H1</option></select></label><label className="field-block"><span>History unit</span><select value={historyMode} onChange={(event) => setHistoryMode(event.target.value as 'candles' | 'days')}><option value="candles">Candles</option><option value="days">Days</option></select></label><label className="field-block"><span>History value</span><input type="number" min="1" max={historyMode === 'candles' ? 10000 : 30} value={historyValue} onChange={(event) => setHistoryValue(Number(event.target.value) || 1)} /></label><label className="field-block"><span>Attempts</span><input type="number" min="1" max="900" value={attempts} onChange={(event) => setAttempts(Number(event.target.value) || 24)} /></label><label className="field-block"><span>Hyperopt loss</span><select value={hyperoptLoss} onChange={(event) => setHyperoptLoss(event.target.value)} disabled={hyperoptLossFunctionsQuery.isLoading}><option value="">{hyperoptLossFunctionsQuery.isLoading ? 'Loading loss functions…' : 'Select loss function'}</option>{hyperoptLossFunctionsQuery.data?.options.map((loss) => <option key={loss} value={loss}>{loss}</option>)}</select></label></div>
            <div className="panel-header compact"><span>Configured strategy timeframe: <strong>{aiConfigQuery.data?.timeframe ?? 'M5'}</strong></span><button className="secondary-action" onClick={handleTimeframeApply} disabled={timeframeMutation.isPending || selectedTimeframe === aiConfigQuery.data?.timeframe}>{timeframeMutation.isPending ? 'Applying…' : 'Apply timeframe'}</button></div>

            {startHyperoptMutation.error && <p>Hyperopt request failed: {startHyperoptMutation.error.message}</p>}
            {stopHyperoptMutation.error && <p>Stop request failed: {stopHyperoptMutation.error.message}</p>}

            {hyperoptStatusQuery.data && (isHyperoptRunning || hyperoptStatusQuery.data.status === 'failed') && (
              <>
                <p>
                  {isHyperoptRunning
                    ? `Running… attempt ${hyperoptStatusQuery.data.attemptsCompleted} of ${hyperoptStatusQuery.data.attemptsTotal || '?'}`
                    : `Hyperopt failed: ${hyperoptStatusQuery.data.error ?? 'unknown error'}`}
                </p>
                {isHyperoptRunning && hyperoptStatusQuery.data.attemptsTotal > 0 && (
                  <div className="hyperopt-progress"><div className="hyperopt-progress-fill" style={{ width: `${Math.min(100, (hyperoptStatusQuery.data.attemptsCompleted / hyperoptStatusQuery.data.attemptsTotal) * 100)}%` }} /></div>
                )}
              </>
            )}

            {hyperoptReportQuery.data?.available && hyperoptReportQuery.data.report && (
              <>
                <div className="panel-header compact" style={{ marginTop: '12px' }}>
                  <span>Last report for <strong>{hyperoptReportQuery.data.pair}</strong></span>
                  <span className="pill neutral">{hyperoptReportQuery.data.ageDays === 0 ? 'today' : `${hyperoptReportQuery.data.ageDays} day(s) ago`}</span>
                </div>
                <div className="bullet-list">
                  <div><span>Loss function</span><strong>{hyperoptReportQuery.data.report.hyperoptLoss ?? hyperoptLoss}</strong></div>
                  <div><span>Best parameters</span><strong>{Object.entries(hyperoptReportQuery.data.report.bestParameters ?? {}).map(([key, value]) => `${key}=${value}`).join(' • ') || 'not available'}</strong></div>
                  <div><span>Train</span><strong>{hyperoptReportQuery.data.report.train.netPl} / DD {hyperoptReportQuery.data.report.train.drawdown} / {hyperoptReportQuery.data.report.train.trades} trades</strong></div>
                  <div><span>Validation</span><strong>{hyperoptReportQuery.data.report.validation.netPl} / DD {hyperoptReportQuery.data.report.validation.drawdown} / {hyperoptReportQuery.data.report.validation.trades} trades</strong></div>
                  <div><span>Objective</span><strong>{hyperoptReportQuery.data.report.objective}</strong></div>
                </div>
                <pre className="hyperopt-report">{hyperoptReportQuery.data.report.reportText}</pre>
                <div className="strategy-table-wrap" style={{ marginTop: '18px' }}>
                  <table className="positions-table">
                    <thead><tr><th>Rank</th><th>Entry</th><th>Max spread</th><th>Objective</th><th>Train P/L</th><th>Validation P/L</th><th>Validation DD</th><th>Trades</th><th>Coverage</th></tr></thead>
                    <tbody>
                      {hyperoptReportQuery.data.report.candidates.slice(0, 5).map((candidate) => (
                        <tr key={`${candidate.entryThreshold}-${candidate.maxSpreadPct}`} className={Number(candidate.validationNetPl) >= 0 ? 'row-profit' : 'row-loss'}>
                          <td>{candidate.rank === 1 ? 'Best' : candidate.rank}</td>
                          <td>{candidate.entryThreshold}</td>
                          <td>{candidate.maxSpreadPct}%</td>
                          <td>{candidate.objective}</td>
                          <td>{candidate.trainNetPl}</td>
                          <td>{candidate.validationNetPl}</td>
                          <td>{candidate.validationDrawdown}</td>
                          <td>{candidate.validationTrades}</td>
                          <td>{candidate.coverage}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            {hyperoptReportQuery.data && !hyperoptReportQuery.data.available && !isHyperoptRunning && <p>No hyperopt report yet for {selectedPair}. Run hyperopt to generate one.</p>}
          </div>

          </div>

          <div className="ai-panel-parent ai-panel-parent-right">
          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Core</p>
                <h3>Model setup</h3>
              </div>
            </div>

            <div className="risk-summary">
              <div>
                <span>Strategy</span>
                <strong>{aiConfigQuery.data.strategyName}</strong>
              </div>
              <div>
                <span>Model</span>
                <strong>{aiConfigQuery.data.model}</strong>
              </div>
              <div>
                <span>Timeframe</span>
                <strong>{aiConfigQuery.data.timeframe}</strong>
              </div>
              <div>
                <span>Risk budget</span>
                <strong>{aiConfigQuery.data.riskBudget}</strong>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Pipeline</p>
                <h3>Feature engineering &amp; training (FreqAI)</h3>
              </div>
              <button className="primary-action" onClick={handleFreqaiSave} disabled={freqaiMutation.isPending}>{freqaiMutation.isPending ? 'Saving…' : 'Save training settings'}</button>
            </div>

            <div className="strategy-stack">
              {FEATURE_OPTIONS.map((feature) => (
                <label key={feature} className="strategy-card" style={{ cursor: 'pointer' }}>
                  <div className="strategy-row">
                    <strong>{feature}</strong>
                    <input type="checkbox" checked={featureSet.includes(feature)} onChange={() => toggleFeature(feature)} />
                  </div>
                </label>
              ))}
            </div>

            <div className="settings-grid" style={{ marginTop: '16px' }}>
              <label className="field-block"><span>Train period (days)</span><input type="number" min="1" max="365" value={freqaiForm.trainPeriodDays} onChange={(event) => setFreqaiForm((current) => ({ ...current, trainPeriodDays: event.target.value }))} /></label>
              <label className="field-block"><span>Backtest period (days)</span><input type="number" min="1" max="90" value={freqaiForm.backtestPeriodDays} onChange={(event) => setFreqaiForm((current) => ({ ...current, backtestPeriodDays: event.target.value }))} /></label>
              <label className="field-block"><span>Label period (candles)</span><input type="number" min="1" max="200" value={freqaiForm.labelPeriodCandles} onChange={(event) => setFreqaiForm((current) => ({ ...current, labelPeriodCandles: event.target.value }))} /></label>
              <label className="field-block"><span>Shifted candles</span><input type="number" min="0" max="20" value={freqaiForm.includeShiftedCandles} onChange={(event) => setFreqaiForm((current) => ({ ...current, includeShiftedCandles: event.target.value }))} /></label>
              <label className="field-block"><span>Indicator periods (candles)</span><input type="text" placeholder="5,14" value={freqaiForm.indicatorPeriodsCandles} onChange={(event) => setFreqaiForm((current) => ({ ...current, indicatorPeriodsCandles: event.target.value }))} /></label>
              <label className="field-block"><span>Weight factor</span><input type="number" step="0.05" min="0" max="0.99" value={freqaiForm.weightFactor} onChange={(event) => setFreqaiForm((current) => ({ ...current, weightFactor: event.target.value }))} /></label>
              <label className="field-block"><span>DI threshold</span><input type="number" step="0.1" min="0" max="10" value={freqaiForm.diThreshold} onChange={(event) => setFreqaiForm((current) => ({ ...current, diThreshold: event.target.value }))} /></label>
            </div>
            <p>These values feed <code>build_forex_ai_dataset</code> and the LightGBM comparison directly: they change the feature schema hash, the train/validation/OOS split, sample recency weighting, and DI-based outlier filtering shown in the comparison panel above.</p>
            {freqaiMutation.error && <p>Save rejected: {freqaiMutation.error.message}</p>}
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Guardrails</p>
                <h3>Safe training policy</h3>
              </div>
            </div>

            <div className="bullet-list">
              <div><span>Mode</span><strong>{aiConfigQuery.data.trainingMode}</strong></div>
              <div><span>Live gating</span><strong>Disabled</strong></div>
              <div><span>Broker path</span><strong>Practice only</strong></div>
              <div><span>Validation</span><strong>Backtest + walk-forward</strong></div>
              <div><span>Execution safety</span><strong>Dry-run + review required</strong></div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Status</p>
                <h3>Operational state</h3>
              </div>
            </div>

            <div className="bullet-list">
              <div><span>Status</span><strong>{effectiveReview.status}</strong></div>
              <div><span>Risk policy</span><strong>{effectiveReview.riskPolicy}</strong></div>
              <div><span>Broker account</span><strong>Practice safe mode</strong></div>
              <div><span>Last update</span><strong>{new Date(effectiveReview.lastUpdated).toLocaleString()}</strong></div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header compact">
              <div>
                <p className="eyebrow">Review</p>
                <h3>Approval workflow</h3>
              </div>
            </div>

            <div className="bullet-list">
              <div><span>Approval state</span><strong>{effectiveReview.status}</strong></div>
              <div><span>Notes</span><strong>{effectiveReview.notes}</strong></div>
            </div>

            <div className="summary-grid" style={{ marginTop: '16px' }}>
              {effectiveReview.guardrails.map((guardrail) => (
                <div key={guardrail} className="summary-card">
                  <span>Guardrail</span>
                  <strong>{guardrail}</strong>
                </div>
              ))}
            </div>

            <div className="summary-grid" style={{ marginTop: '18px' }}>
              <button className="primary-action" onClick={() => handleReview('approved')} disabled={reviewMutation.isPending}>
                {reviewMutation.isPending ? 'Saving…' : 'Approve strategy'}
              </button>
              <button className="secondary-action" onClick={() => handleReview('rejected')} disabled={reviewMutation.isPending}>
                Reject strategy
              </button>
            </div>
            <p>Approval uses the selected pair/timeframe Hyperopt revision. Independent Backtest is optional and can be run separately for research evidence.</p>
            {effectiveReview.approvedRevision && <p>Approved revision: {effectiveReview.approvedRevision.pair} / {effectiveReview.approvedRevision.timeframe} at {effectiveReview.approvedRevision.approvedAt ? new Date(effectiveReview.approvedRevision.approvedAt).toLocaleString() : 'recorded'}.</p>}
            {reviewMutation.error && <p>Approval rejected: {reviewMutation.error.message}</p>}
          </div>
          </div>
        </section>
      )}
    </>
  )
}
