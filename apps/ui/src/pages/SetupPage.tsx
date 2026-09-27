import { useMutation, useQuery } from '@tanstack/react-query'
import { startTransition, useEffect, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { controlRuntime, discoverSetupAccounts, getAvailableStrategies, getRuntimeStatus, getSetupFileUrl, getSetupInstruments, getSetupStatus, saveSetup, uploadSetupFile, type SetupAccount, type SetupPayload } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

export function SetupPage() {
  const navigate = useNavigate()
  const sharedInstruments = useUiStore((state) => state.selectedInstruments)
  const setSelectedInstruments = useUiStore((state) => state.setSelectedInstruments)
  const [form, setForm] = useState<SetupPayload>({
    token: '',
    accountId: '',
    accountTypeCode: '',
    accountConfirmed: false,
    liveConfirmed: false,
    environment: 'practice',
    instruments: sharedInstruments.map((item) => item.replace('/', '_')),
    pairTimeframes: Object.fromEntries(sharedInstruments.map((item) => [item.replace('/', '_'), item.includes('GBP') ? '1h' : '5m'])),
    pairStrategies: Object.fromEntries(sharedInstruments.map((item) => [item.replace('/', '_'), 'ForexAIStrategyBaseline'])),
    riskFraction: '0.01',
  })
  const [error, setError] = useState<string | null>(null)
  const [operationMessage, setOperationMessage] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<SetupAccount[]>([])
  const [selectedAccountId, setSelectedAccountId] = useState('')
  const [strategyUploadMessage, setStrategyUploadMessage] = useState<string | null>(null)
  const mutation = useMutation({ mutationFn: saveSetup })
  const discoveryMutation = useMutation({ mutationFn: discoverSetupAccounts })
  const runtimeQuery = useQuery({ queryKey: ['setup-runtime'], queryFn: getRuntimeStatus })
  const strategiesQuery = useQuery({ queryKey: ['available-strategies'], queryFn: getAvailableStrategies })
  const savedSetupQuery = useQuery({ queryKey: ['setup-status'], queryFn: getSetupStatus })
  const runtimeMutation = useMutation({ mutationFn: controlRuntime, onSuccess: (result) => { setOperationMessage(result.message); void runtimeQuery.refetch() } })
  const uploadMutation = useMutation({ mutationFn: ({ kind, file }: { kind: 'config' | 'strategy'; file: File }) => uploadSetupFile(kind, file), onSuccess: () => setOperationMessage('File uploaded. Reload the bot before the next cycle.') })
  const instrumentsQuery = useQuery({
    queryKey: ['setup-instruments', form.environment, form.token, selectedAccountId],
    queryFn: () => getSetupInstruments({ token: form.token, accountId: selectedAccountId, environment: form.environment }),
    enabled: Boolean(form.token.trim() && selectedAccountId && form.environment),
    retry: false,
  })

  useEffect(() => {
    setSelectedInstruments(form.instruments.map((item) => item.replace(/_/g, '/')))
  }, [form.instruments, setSelectedInstruments])

  useEffect(() => {
    const saved = savedSetupQuery.data
    if (!saved?.configured || !saved.instruments.length) return
    setForm((current) => ({
      ...current,
      instruments: saved.instruments,
      pairTimeframes: { ...current.pairTimeframes, ...saved.pairTimeframes },
      pairStrategies: { ...current.pairStrategies, ...saved.pairStrategies },
    }))
  }, [savedSetupQuery.data])

  useEffect(() => {
    const defaultStrategy = strategiesQuery.data?.[0]?.name
    if (!defaultStrategy) return
    startTransition(() => {
      setForm((current) => {
        const missing = current.instruments.filter((pair) => !current.pairStrategies[pair])
        if (!missing.length) return current
        return {
          ...current,
          pairStrategies: Object.fromEntries([
            ...Object.entries(current.pairStrategies),
            ...missing.map((pair) => [pair, defaultStrategy]),
          ]),
        }
      })
    })
  }, [form.instruments, form.pairStrategies, strategiesQuery.data])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null)
    mutation.mutate(form, {
      onSuccess: () => {
        setSelectedInstruments(form.instruments.map((item) => item.replace(/_/g, '/')))
        navigate('/', { replace: true })
      },
      onError: (reason) => setError(reason instanceof Error ? reason.message : 'Setup was rejected'),
    })
  }

  const addInstrument = (instrument: string) => {
    setForm((current) => {
      if (current.instruments.includes(instrument)) return current
      return {
        ...current,
        instruments: [...current.instruments, instrument],
        pairTimeframes: {
          ...current.pairTimeframes,
          [instrument]: current.pairTimeframes[instrument] ?? '5m',
        },
        pairStrategies: {
          ...current.pairStrategies,
          [instrument]: current.pairStrategies[instrument] ?? strategiesQuery.data?.[0]?.name ?? 'ForexAIStrategyBaseline',
        },
      }
    })
  }

  const removeInstrument = (instrument: string) => {
    setForm((current) => {
      if (!current.instruments.includes(instrument)) return current
      const nextInstruments = current.instruments.filter((item) => item !== instrument)
      const nextTimeframes = { ...current.pairTimeframes }
      const nextStrategies = { ...current.pairStrategies }
      delete nextTimeframes[instrument]
      delete nextStrategies[instrument]
      return {
        ...current,
        instruments: nextInstruments,
        pairTimeframes: nextTimeframes,
        pairStrategies: nextStrategies,
      }
    })
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
  const handleStrategyFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    setStrategyUploadMessage(null)
    for (const file of files) {
      try {
        const result = await uploadMutation.mutateAsync({ kind: 'strategy', file })
        setStrategyUploadMessage(`${file.name}: ${result.strategyNames?.join(', ') ?? 'validated and uploaded'}`)
        await strategiesQuery.refetch()
      } catch (reason) {
        setStrategyUploadMessage(reason instanceof Error ? reason.message : `${file.name} was rejected`)
        break
      }
    }
  }
  const selectedAccount = accounts.find((account) => account.accountId === selectedAccountId)
  const discoverAccounts = () => {
    setError(null)
    setAccounts([])
    setSelectedAccountId('')
    setForm((current) => ({ ...current, accountId: '', accountTypeCode: '', accountConfirmed: false, liveConfirmed: false }))
    discoveryMutation.mutate({ token: form.token, environment: form.environment }, {
      onSuccess: (result) => setAccounts(result.accounts),
      onError: (reason) => setError(reason instanceof Error ? reason.message : 'OANDA account discovery failed'),
    })
  }
  const selectAccount = (account: SetupAccount) => {
    if (!account.summaryAccessible) return
    setSelectedAccountId(account.accountId)
    setForm((current) => ({ ...current, accountId: account.accountId, accountTypeCode: account.accountTypeCode, accountConfirmed: false, liveConfirmed: false }))
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
            <p className="setup-kicker">01 / TRADING MODE</p>
            <h1 id="setup-title">Bring the workspace online.</h1>
            <p>Choose Practice for demo orders or Live for real orders. The selected mode chooses the matching OANDA API and token.</p>
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
              <h2>Discover your OANDA accounts</h2>
            </div>
            <span className={`setup-mode-badge ${form.environment === 'live' ? 'live' : ''}`}>{form.environment === 'live' ? 'LIVE' : 'PRACTICE'}</span>
          </div>
          <p className="setup-intro">Account discovery uses read-only requests. No order is created or changed. Your token is stored only after you confirm a supported account.</p>

          <form className="setup-form" onSubmit={submit}>
            <div className="setup-section-heading"><span>01</span><div><strong>Choose your trading mode</strong><small>Mode, API environment, and order execution are linked.</small></div></div>
            <div className="setup-environment-switch field-wide" role="group" aria-label="Trading mode">
              <button type="button" aria-pressed={form.environment === 'practice'} className={form.environment === 'practice' ? 'selected' : ''} onClick={() => { discoveryMutation.reset(); setAccounts([]); setSelectedAccountId(''); setForm((current) => ({ ...current, token: '', environment: 'practice', accountId: '', accountTypeCode: '', accountConfirmed: false, liveConfirmed: false })) }}><strong>Practice</strong><small>Practice API · demo orders</small></button>
              <button type="button" aria-pressed={form.environment === 'live'} className={form.environment === 'live' ? 'selected' : ''} onClick={() => { discoveryMutation.reset(); setAccounts([]); setSelectedAccountId(''); setForm((current) => ({ ...current, token: '', environment: 'live', accountId: '', accountTypeCode: '', accountConfirmed: false, liveConfirmed: false })) }}><strong>Live</strong><small>Live API · real orders</small></button>
            </div>
            {form.environment === 'live' && <p className="setup-live-note field-wide">Live uses the real OANDA API and can place orders against real funds. Saving requires <code>OANDA_LIVE_CONFIRM=1</code> on the server and the explicit confirmations below. Test using Practice first.</p>}
            <label className="field-block field-wide">
              <span>OANDA {form.environment === 'live' ? 'Live' : 'Practice'} token</span>
              <input type="password" autoComplete="new-password" required value={form.token} onChange={(event) => { discoveryMutation.reset(); setAccounts([]); setSelectedAccountId(''); setForm((current) => ({ ...current, token: event.target.value, accountId: '', accountTypeCode: '', accountConfirmed: false, liveConfirmed: false })) }} />
              <small>Sent to OANDA only for read-only account discovery. Saved on this server only after confirmation.</small>
            </label>
            <div className="field-wide discovery-action"><button type="button" className="primary-action" onClick={discoverAccounts} disabled={!form.token.trim() || discoveryMutation.isPending}>{discoveryMutation.isPending ? 'Checking OANDA...' : 'Discover accounts'}</button>{discoveryMutation.isPending && <span>Reading account types and summaries. No trading request will be sent.</span>}</div>

            {discoveryMutation.isSuccess && <section className="account-discovery field-wide" aria-live="polite"><div className="account-discovery-heading"><div><strong>{accounts.length ? 'Supported accounts found' : 'No supported accounts found'}</strong><small>{accounts.length} eligible · {discoveryMutation.data.excludedAccountCount} other account(s) excluded</small></div><span>READ ONLY</span></div>{accounts.length > 0 && <div className="account-choice-list">{accounts.map((account) => { const summary = account.summary; const selected = account.accountId === selectedAccountId; return <button type="button" key={account.accountId} className={`account-choice ${selected ? 'selected' : ''} ${!account.summaryAccessible ? 'unavailable' : ''}`} aria-pressed={selected} disabled={!account.summaryAccessible} onClick={() => selectAccount(account)}><span className="account-choice-radio" aria-hidden="true"/><span className="account-choice-main"><span className="account-choice-title">{summary?.alias || 'OANDA account'}<span className={`account-type-code code-${account.accountTypeCode}`}>{account.accountTypeCode} · {account.accountType}</span></span><span className="account-choice-id">{account.accountId}</span><span className="account-choice-meta">{summary?.currency || 'Currency unavailable'}{summary?.NAV ? ` · NAV ${summary.NAV}` : ''}{summary?.marginAvailable ? ` · Margin available ${summary.marginAvailable}` : ''}</span>{!account.summaryAccessible && <span className="account-choice-warning">Account summary could not be read; this account cannot be selected.</span>}</span></button> })}</div>}</section>}

            {selectedAccount && <label className="account-confirm field-wide"><input type="checkbox" checked={form.accountConfirmed} onChange={(event) => setForm((current) => ({ ...current, accountConfirmed: event.target.checked }))}/><span>I confirm account {selectedAccount.accountId} is the {selectedAccount.accountType} ({selectedAccount.accountTypeCode}) account I want to connect in {form.environment.toUpperCase()}.</span></label>}
            {selectedAccount && form.environment === 'live' && <label className="account-confirm live-confirm field-wide"><input type="checkbox" checked={form.liveConfirmed} onChange={(event) => setForm((current) => ({ ...current, liveConfirmed: event.target.checked }))}/><span>This is a real Live account. I understand that saving Live mode routes order execution to OANDA Live and can affect real funds.</span></label>}

            <div className="setup-section-heading"><span>02</span><div><strong>Risk and market defaults</strong><small>These settings apply to the selected Practice or Live account.</small></div></div>
            <label className="field-block">
              <span>Risk fraction</span>
              <input type="number" min="0.0001" max="1" step="0.0001" required value={form.riskFraction} onChange={(event) => setForm((current) => ({ ...current, riskFraction: event.target.value }))} />
              <small>0.01 means 1% of account equity per risk unit.</small>
            </label>
            <div className="field-block field-wide">
              <span>Instruments</span>
              {instrumentsQuery.isLoading && <small>Loading available OANDA pairs...</small>}
              {instrumentsQuery.isError && <small className="field-error">{instrumentsQuery.error instanceof Error ? instrumentsQuery.error.message : 'Unable to load the instrument list.'}</small>}
              <div className="instrument-selector" aria-live="polite">
                {(instrumentsQuery.data?.instruments ?? []).map((instrument) => {
                  const selected = form.instruments.includes(instrument.name)
                  return (
                    <button
                      type="button"
                      key={instrument.name}
                      className={`instrument-option ${selected ? 'selected' : ''} priority-${instrument.priority}`}
                      onClick={() => (selected ? removeInstrument(instrument.name) : addInstrument(instrument.name))}
                      aria-pressed={selected}
                    >
                      <span>{instrument.displayName}</span>
                      <small>{instrument.priority === 'high' ? 'Priority' : instrument.priority === 'medium' ? 'Watch' : 'Available'}</small>
                    </button>
                  )
                })}
                {instrumentsQuery.data && instrumentsQuery.data.instruments.length === 0 && <small>No OANDA instruments were returned for this account.</small>}
                {!instrumentsQuery.data && !instrumentsQuery.isLoading && !instrumentsQuery.isError && <small>Choose an account to load the OANDA instrument list.</small>}
              </div>
              <small>Use the list to add or remove pairs for this setup. The selected pairs are saved in the config when you click Save.</small>
            </div>

            <div className="setup-section-heading"><span>03</span><div><strong>Pair timeframes</strong><small>Each instrument can run on its own candle timeframe.</small></div></div>
            <div className="pair-timeframe-list field-wide">
              {form.instruments.map((instrument) => <div className="pair-timeframe-row" key={instrument}><span>{instrument.replace('_', '/')}</span><select aria-label={`${instrument.replace('_', '/')} timeframe`} value={form.pairTimeframes[instrument] ?? '5m'} onChange={(event) => updatePairTimeframe(instrument, event.target.value)}><option value="1m">1m</option><option value="5m">5m</option><option value="15m">15m</option><option value="30m">30m</option><option value="1h">1h</option><option value="2h">2h</option><option value="4h">4h</option><option value="6h">6h</option><option value="8h">8h</option><option value="12h">12h</option><option value="1d">1d</option><option value="1w">1w</option><option value="1mo">1mo</option></select><select aria-label={`${instrument.replace('_', '/')} strategy`} value={form.pairStrategies[instrument] ?? strategiesQuery.data?.[0]?.name ?? ''} onChange={(event) => setForm((current) => ({ ...current, pairStrategies: { ...current.pairStrategies, [instrument]: event.target.value } }))} disabled={strategiesQuery.isLoading || !strategiesQuery.data?.length}><option value="" disabled>Select strategy</option>{strategiesQuery.data?.map((strategy) => <option key={strategy.name} value={strategy.name}>{strategy.name}</option>)}</select></div>)}
            </div>

            {error && <p className="setup-error" role="alert">{error}</p>}
            <div className="setup-footer">
              <span className="setup-secure-note">{form.environment === 'live' ? 'Live account · server-side config' : 'Practice environment · server-side config'}</span>
              <button className="primary-action setup-submit" type="submit" disabled={mutation.isPending || !selectedAccount || !form.accountConfirmed || (form.environment === 'live' && !form.liveConfirmed)}>
                {mutation.isPending ? 'Saving configuration...' : `Save ${form.environment === 'live' ? 'Live' : 'Practice'} mode`}
              </button>
            </div>
          </form>

          <section className="setup-operations" aria-labelledby="operations-title">
            <div className="setup-section-heading"><span>04</span><div><strong id="operations-title">Operations</strong><small>Apply file changes and control the bot without leaving setup.</small></div></div>
            <div className="setup-file-grid">
              <div className="setup-file-card"><div><strong>Strategy files</strong><small>Upload one or more validated Freqtrade Python strategies. Class names must be unique.</small></div><div className="setup-file-actions"><label className="secondary-action">Upload<input type="file" accept=".py,text/x-python" multiple onChange={handleStrategyFiles} /></label><a className="secondary-action" href={getSetupFileUrl('strategy')} download>Download active</a></div>{strategyUploadMessage && <small role="status">{strategyUploadMessage}</small>}{strategiesQuery.data?.map((strategy) => <a key={strategy.name} href={getSetupFileUrl('strategy', strategy.name)} download>{strategy.name} · {strategy.fileName}</a>)}</div>
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
