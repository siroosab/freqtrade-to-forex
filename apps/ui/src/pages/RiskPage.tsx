import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ForexChart } from '../components/ForexChart'
import { getOrdersChart, getRiskConfig, getRiskSummary, saveRiskConfig, type RiskConfig } from '../api/mockApi'

export function RiskPage() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['risk'], queryFn: getRiskSummary })
  const [pair, setPair] = useState('EUR/USD')
  const [timeframe, setTimeframe] = useState('M15')
  const configQuery = useQuery({ queryKey: ['risk-config', pair], queryFn: () => getRiskConfig(pair) })
  const chartQuery = useQuery({ queryKey: ['risk-chart', pair, timeframe], queryFn: () => getOrdersChart(pair, timeframe), refetchInterval: 30000 })
  const saveMutation = useMutation({ mutationFn: saveRiskConfig, onSuccess: () => void configQuery.refetch() })
  const [form, setForm] = useState<RiskConfig | null>(null)
  const [selection, setSelection] = useState<'stopLoss' | 'takeProfit' | 'averageEntry'>('stopLoss')
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null)

  useEffect(() => { if (configQuery.data) setForm(configQuery.data) }, [configQuery.data])
  const update = (key: keyof RiskConfig, value: string | null) => setForm((current) => current ? { ...current, [key]: value } : current)

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
        <div className="panel-header compact"><div><p className="eyebrow">Risk map</p><h3>Price-based protection</h3></div><div className="chart-controls"><select aria-label="Risk pair" value={pair} onChange={(event) => setPair(event.target.value)}><option>EUR/USD</option><option>GBP/USD</option><option>USD/JPY</option></select><select aria-label="Risk timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}><option>M5</option><option>M15</option><option>H1</option></select></div></div>
        <p className="risk-helper">Select a protection level, then click the chart. Unconfigured fields use the default risk policy.</p>
        <div className="chart-controls risk-click-controls"><button type="button" className={selection === 'stopLoss' ? 'selected' : ''} onClick={() => setSelection('stopLoss')}>Set stop loss</button><button type="button" className={selection === 'takeProfit' ? 'selected' : ''} onClick={() => setSelection('takeProfit')}>Set take profit</button><button type="button" className={selection === 'averageEntry' ? 'selected' : ''} onClick={() => setSelection('averageEntry')}>Set average entry</button></div>
        {chartQuery.isLoading && <p>Loading broker chart…</p>}
        {chartQuery.isError && <p>Risk chart data unavailable from broker API.</p>}
        {chartQuery.data && <ForexChart data={chartQuery.data} onPriceSelect={(price) => update(selection, price.toFixed(5))} />}
      </section>

      <section className="content-grid">
        <div className="panel"><div className="panel-header compact"><div><p className="eyebrow">Pre-trade</p><h3>Before order controls</h3></div><span className="pill neutral">{form?.source ?? 'default-policy'}</span></div>
          {form && <div className="settings-grid"><label className="field-block"><span>Units</span><input value={form.units} onChange={(event) => update('units', event.target.value)} /></label><label className="field-block"><span>Risk budget</span><div className="value-mode"><input value={form.riskBudget} onChange={(event) => update('riskBudget', event.target.value)} /><select value={form.riskBudgetMode} onChange={(event) => update('riskBudgetMode', event.target.value)}><option value="percent">%</option><option value="absolute">Amount</option></select></div></label><label className="field-block"><span>Leverage</span><input value={form.leverage} onChange={(event) => update('leverage', event.target.value)} /></label><label className="field-block"><span>Max exposure</span><div className="value-mode"><input value={form.maxExposure} onChange={(event) => update('maxExposure', event.target.value)} /><select value={form.maxExposureMode} onChange={(event) => update('maxExposureMode', event.target.value)}><option value="absolute">Amount</option><option value="percent">%</option></select></div></label><label className="field-block"><span>Side</span><select value={form.side} onChange={(event) => update('side', event.target.value)}><option>LONG</option><option>SHORT</option><option>BOTH</option><option>NONE</option></select></label></div>}
        </div>
        <div className="panel"><div className="panel-header compact"><div><p className="eyebrow">Post-trade</p><h3>After order controls</h3></div><button className="primary-action" onClick={() => { if (form) { setSaveFeedback(null); saveMutation.mutate(form, { onSuccess: () => setSaveFeedback('Risk policy saved and ready for the next order.'), onError: () => setSaveFeedback('Risk policy was rejected by the backend.') }) } }} disabled={!form || saveMutation.isPending}>{saveMutation.isPending ? 'Saving…' : 'Save risk policy'}</button></div>
          {saveFeedback && <p className="risk-save-feedback" role="status">{saveFeedback}</p>}
          {form && <div className="settings-grid"><label className="field-block"><span>Stop loss</span><div className="value-mode"><input value={form.stopLoss ?? ''} placeholder="Chart or value" onChange={(event) => update('stopLoss', event.target.value || null)} /><select value={form.stopLossMode} onChange={(event) => update('stopLossMode', event.target.value)}><option value="price">Price</option><option value="percent">%</option></select></div></label><label className="field-block"><span>Take profit</span><div className="value-mode"><input value={form.takeProfit ?? ''} placeholder="Chart or value" onChange={(event) => update('takeProfit', event.target.value || null)} /><select value={form.takeProfitMode} onChange={(event) => update('takeProfitMode', event.target.value)}><option value="price">Price</option><option value="percent">%</option></select></div></label><label className="field-block"><span>Average entry</span><div className="value-mode"><input value={form.averageEntry ?? ''} placeholder="Chart or value" onChange={(event) => update('averageEntry', event.target.value || null)} /><select value={form.averageEntryMode} onChange={(event) => update('averageEntryMode', event.target.value)}><option value="price">Price</option><option value="percent">%</option></select></div></label><label className="field-block"><span>Max averaging adds</span><input value={form.maxAdds} onChange={(event) => update('maxAdds', event.target.value)} /></label></div>}
        </div>
      </section>

      {isLoading && <div className="panel page-panel"><p>Loading risk data from backend…</p></div>}
      {isError && <div className="panel page-panel"><p>Backend risk endpoint unavailable; fallback data is being used.</p></div>}
      {!isLoading && data && <section className="content-grid"><div className="panel"><div className="panel-header compact"><div><p className="eyebrow">Policy</p><h3>Current limits</h3></div></div><div className="risk-summary"><div><span>Daily loss</span><strong>{data.dailyLoss}</strong></div><div><span>Max exposure</span><strong>{data.maxExposure}</strong></div><div><span>Margin level</span><strong>{data.marginLevel}</strong></div><div><span>Kill switch</span><strong>{data.killSwitch ? 'Active' : 'Clear'}</strong></div></div></div><div className="panel"><div className="panel-header compact"><div><p className="eyebrow">Exposure</p><h3>By symbol</h3></div></div><div className="strategy-stack">{data.exposureByPair.map((item) => <div key={item.pair} className="strategy-card"><div className="strategy-row"><strong>{item.pair}</strong><span className="pill neutral">{item.value}</span></div></div>)}</div></div></section>}
    </>
  )
}
