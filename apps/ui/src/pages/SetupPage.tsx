import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { controlRuntime, getRuntimeStatus, getSetupFileUrl, saveSetup, uploadSetupFile, type SetupPayload } from '../api/mockApi'

export function SetupPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState<SetupPayload>({
    token: '',
    accountId: '',
    environment: 'practice',
    executionMode: 'dry_run',
    instruments: ['EUR_USD', 'GBP_USD'],
    pairTimeframes: { EUR_USD: '5m', GBP_USD: '1h' },
    riskFraction: '0.01',
  })
  const [error, setError] = useState<string | null>(null)
  const [operationMessage, setOperationMessage] = useState<string | null>(null)
  const mutation = useMutation({ mutationFn: saveSetup })
  const runtimeQuery = useQuery({ queryKey: ['setup-runtime'], queryFn: getRuntimeStatus })
  const runtimeMutation = useMutation({ mutationFn: controlRuntime, onSuccess: (result) => { setOperationMessage(result.message); void runtimeQuery.refetch() } })
  const uploadMutation = useMutation({ mutationFn: ({ kind, file }: { kind: 'config' | 'strategy'; file: File }) => uploadSetupFile(kind, file), onSuccess: () => setOperationMessage('File uploaded. Reload the bot before the next cycle.') })

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null)
    mutation.mutate(form, {
      onSuccess: () => navigate('/', { replace: true }),
      onError: (reason) => setError(reason instanceof Error ? reason.message : 'Setup was rejected'),
    })
  }

  const updateInstruments = (value: string) => {
    const instruments = value.split(',').map((item) => item.trim().toUpperCase().replace('/', '_')).filter(Boolean)
    setForm((current) => ({
      ...current,
      instruments,
      pairTimeframes: Object.fromEntries(instruments.map((instrument) => [instrument, current.pairTimeframes[instrument] ?? '5m'])),
    }))
  }

  const updatePairTimeframe = (pair: string, timeframe: string) => setForm((current) => ({ ...current, pairTimeframes: { ...current.pairTimeframes, [pair]: timeframe } }))
  const runRuntimeAction = (action: 'reload' | 'resume' | 'pause' | 'stop') => {
    if (action === 'stop' && !window.confirm('Stop the bot completely? New trades and open-trade management will both be disabled.')) return
    setOperationMessage(null)
    runtimeMutation.mutate(action)
  }
  const handleFile = (kind: 'config' | 'strategy', event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) uploadMutation.mutate({ kind, file })
    event.target.value = ''
  }

  return (
    <main className="setup-shell">
      <section className="setup-card" aria-labelledby="setup-title">
        <aside className="setup-aside">
          <div className="setup-brand">
            <div className="brand-mark">FX</div>
            <div>
              <p className="eyebrow">FIRST-RUN SETUP</p>
              <strong>Forex Control</strong>
            </div>
          </div>
          <div className="setup-aside-copy">
            <p className="setup-kicker">01 / CONNECTION</p>
            <h1 id="setup-title">Bring the workspace online.</h1>
            <p>Connect the bot to an OANDA Practice account, choose its safe starting mode, and open the dashboard.</p>
          </div>
          <div className="setup-checklist" aria-label="Setup steps">
            <div className="setup-check active"><span>01</span><div><strong>Broker connection</strong><small>Account and market access</small></div></div>
            <div className="setup-check"><span>02</span><div><strong>Risk boundaries</strong><small>Instrument and sizing defaults</small></div></div>
            <div className="setup-check"><span>03</span><div><strong>Operator dashboard</strong><small>Review before execution</small></div></div>
          </div>
        </aside>
        <div className="setup-main">
          <div className="setup-main-header">
            <div>
              <p className="setup-kicker">ACCOUNT SETTINGS</p>
              <h2>Connect your Practice account</h2>
            </div>
            <span className="setup-mode-badge">PRACTICE ONLY</span>
          </div>
          <p className="setup-intro">The terminal created the workspace. Finish the connection here; credentials are written only to the server-side config.</p>

          <form className="setup-form" onSubmit={submit}>
            <div className="setup-section-heading"><span>01</span><div><strong>Broker access</strong><small>Use credentials from your OANDA Practice account.</small></div></div>
            <label className="field-block field-wide">
              <span>OANDA Practice token</span>
              <input type="password" autoComplete="new-password" required value={form.token} onChange={(event) => setForm((current) => ({ ...current, token: event.target.value }))} />
              <small>Stored on the server. Never shown in the dashboard.</small>
            </label>
            <label className="field-block field-wide">
              <span>Practice account ID</span>
              <input required placeholder="101-..." value={form.accountId} onChange={(event) => setForm((current) => ({ ...current, accountId: event.target.value }))} />
            </label>

            <div className="setup-section-heading"><span>02</span><div><strong>Execution guardrails</strong><small>Start conservatively and widen only after review.</small></div></div>
            <label className="field-block">
              <span>Execution mode</span>
              <select value={form.executionMode} onChange={(event) => setForm((current) => ({ ...current, executionMode: event.target.value as SetupPayload['executionMode'] }))}>
                <option value="dry_run">Dry-run · no broker orders</option>
                <option value="practice">Practice · guarded orders</option>
              </select>
            </label>
            <label className="field-block">
              <span>Risk fraction</span>
              <input type="number" min="0.0001" max="1" step="0.0001" required value={form.riskFraction} onChange={(event) => setForm((current) => ({ ...current, riskFraction: event.target.value }))} />
              <small>0.01 means 1% of account equity per risk unit.</small>
            </label>
            <label className="field-block field-wide">
              <span>Instruments</span>
              <input value={form.instruments.join(', ')} onChange={(event) => updateInstruments(event.target.value)} placeholder="EUR_USD, GBP_USD" />
              <small>Comma-separated OANDA instrument names.</small>
            </label>

            <div className="setup-section-heading"><span>03</span><div><strong>Pair timeframes</strong><small>Each instrument can run on its own candle timeframe.</small></div></div>
            <div className="pair-timeframe-list field-wide">
              {form.instruments.map((instrument) => <label className="pair-timeframe-row" key={instrument}><span>{instrument.replace('_', '/')}</span><select value={form.pairTimeframes[instrument] ?? '5m'} onChange={(event) => updatePairTimeframe(instrument, event.target.value)}><option value="1m">1m</option><option value="5m">5m</option><option value="15m">15m</option><option value="30m">30m</option><option value="1h">1h</option><option value="4h">4h</option><option value="1d">1d</option><option value="1w">1w</option></select></label>)}
            </div>

            {error && <p className="setup-error" role="alert">{error}</p>}
            <div className="setup-footer">
              <span className="setup-secure-note">Practice environment · server-side config</span>
              <button className="primary-action setup-submit" type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? 'Saving configuration...' : 'Save and open dashboard'}
              </button>
            </div>
          </form>

          <section className="setup-operations" aria-labelledby="operations-title">
            <div className="setup-section-heading"><span>04</span><div><strong id="operations-title">Operations</strong><small>Apply file changes and control the bot without leaving setup.</small></div></div>
            <div className="setup-file-grid">
              <div className="setup-file-card"><div><strong>Strategy file</strong><small>Upload Python strategy or download the active copy.</small></div><div className="setup-file-actions"><label className="secondary-action">Upload<input type="file" accept=".py,text/x-python" onChange={(event) => handleFile('strategy', event)} /></label><a className="secondary-action" href={getSetupFileUrl('strategy')} download>Download</a></div></div>
              <div className="setup-file-card"><div><strong>config.json</strong><small>Upload a validated JSON config or export the current one.</small></div><div className="setup-file-actions"><label className="secondary-action">Upload<input type="file" accept="application/json,.json" onChange={(event) => handleFile('config', event)} /></label><a className="secondary-action" href={getSetupFileUrl('config')} download>Download</a></div></div>
            </div>
            <div className="runtime-control-card">
              <div><strong>Bot runtime</strong><small>{runtimeQuery.data?.message ?? 'Checking runtime state...'}</small></div>
              <div className="runtime-actions"><button type="button" className="secondary-action" onClick={() => runRuntimeAction('reload')} disabled={runtimeMutation.isPending}>Reload</button>{runtimeQuery.data?.state === 'paused' || runtimeQuery.data?.state === 'stopped' ? <button type="button" className="secondary-action" onClick={() => runRuntimeAction('resume')} disabled={runtimeMutation.isPending}>Resume</button> : <button type="button" className="secondary-action" onClick={() => runRuntimeAction('pause')} disabled={runtimeMutation.isPending}>Pause</button>}<button type="button" className="danger-action" onClick={() => runRuntimeAction('stop')} disabled={runtimeMutation.isPending}>Stop bot</button></div>
            </div>
            {operationMessage && <p className="setup-operation-message" role="status">{operationMessage}</p>}
            {uploadMutation.isError && <p className="setup-error" role="alert">{uploadMutation.error instanceof Error ? uploadMutation.error.message : 'File upload was rejected'}</p>}
          </section>
        </div>
      </section>
    </main>
  )
}
