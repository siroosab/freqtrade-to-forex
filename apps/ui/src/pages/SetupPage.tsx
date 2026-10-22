import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { controlRuntime, controlSystemdService, discoverSetupAccounts, getAvailableStrategies, getAutoExecutionStatus, getRuntimeStatus, getSetupFileUrl, getSetupInstruments, getSetupStatus, getSystemdServiceStatus, saveSetup, saveSetupInstruments, setAutoExecution, setPortfolioCockpitVisibility, uploadSetupFile, type SetupAccount, type SetupPayload, type SetupStatus, type SystemdServiceAction } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

function normalizeSetupInstrument(instrument: string) {
  return instrument.trim().toUpperCase().replace(/\//g, '_')
}

export function SetupPage() {
  const queryClient = useQueryClient()
  const userRole = useUiStore((state) => state.userRole)
  const setSelectedInstruments = useUiStore((state) => state.setSelectedInstruments)
  const [form, setForm] = useState<SetupPayload>({
    token: '',
    accountId: '',
    accountTypeCode: '',
    accountConfirmed: false,
    liveConfirmed: false,
    environment: 'practice',
    instruments: [],
  })
  const [error, setError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [operationMessage, setOperationMessage] = useState<string | null>(null)
  const [startAfterSave, setStartAfterSave] = useState(false)
  const [accounts, setAccounts] = useState<SetupAccount[]>([])
  const [selectedAccountId, setSelectedAccountId] = useState('')
  const [strategyUploadMessage, setStrategyUploadMessage] = useState<string | null>(null)
  const [configUploadMessage, setConfigUploadMessage] = useState<string | null>(null)
  const [instrumentSearch, setInstrumentSearch] = useState('')
  const setupBaselineLoaded = useRef(false)
  const mutation = useMutation({ mutationFn: saveSetup })
  const instrumentsMutation = useMutation({ mutationFn: saveSetupInstruments })
  const startExecutionMutation = useMutation({ mutationFn: () => setAutoExecution(true) })
  const discoveryMutation = useMutation({ mutationFn: discoverSetupAccounts })
  const runtimeQuery = useQuery({ queryKey: ['setup-runtime'], queryFn: getRuntimeStatus })
  const strategiesQuery = useQuery({ queryKey: ['available-strategies'], queryFn: getAvailableStrategies })
  const savedSetupQuery = useQuery({ queryKey: ['setup-status'], queryFn: getSetupStatus })
  const dashboardPreferenceMutation = useMutation({
    mutationFn: setPortfolioCockpitVisibility,
    onSuccess: (result) => {
      queryClient.setQueryData<SetupStatus>(['setup-status'], (current) => current
        ? { ...current, showPortfolioCockpit: result.showPortfolioCockpit }
        : current)
      void queryClient.invalidateQueries({ queryKey: ['setup-status'] })
    },
  })
  const autoExecutionQuery = useQuery({
    queryKey: ['auto-execution-status'],
    queryFn: getAutoExecutionStatus,
    refetchInterval: 5_000,
    retry: 2,
  })
  const runtimeMutation = useMutation({ mutationFn: controlRuntime, onSuccess: (result) => { setOperationMessage(result.message); void runtimeQuery.refetch() } })
  const serviceQuery = useQuery({
    queryKey: ['setup-systemd-service'],
    queryFn: getSystemdServiceStatus,
    enabled: userRole === 'admin',
    refetchInterval: 10_000,
    retry: false,
  })
  const serviceMutation = useMutation({
    mutationFn: controlSystemdService,
    onSuccess: () => void serviceQuery.refetch(),
  })
  const uploadMutation = useMutation({
    mutationFn: ({ kind, file }: { kind: 'config' | 'strategy'; file: File }) => uploadSetupFile(kind, file),
    onSuccess: (result, variables) => {
      if (variables.kind === 'config' && result.liveTradingApprovalRevoked) {
        setOperationMessage('Config uploaded. Previous Live approval was revoked; confirm the Live account again in Setup before trading.')
        setConfigUploadMessage('✅ Config uploaded. Reconfirm the account before enabling Live trading.')
      } else if (variables.kind === 'config' && result.autoExecutionStopped) {
        setOperationMessage('Config uploaded. Automatic execution was stopped; reload the bot before the next cycle.')
        setConfigUploadMessage('✅ Config uploaded. Reload the bot to apply its settings.')
      } else if (variables.kind === 'config') {
        setOperationMessage('File uploaded. Reload the bot before the next cycle.')
        setConfigUploadMessage('✅ Config uploaded successfully. Reload the bot to apply its settings.')
      } else {
        setOperationMessage('File uploaded. Reload the bot before the next cycle.')
      }
      if (variables.kind === 'config') {
        void queryClient.invalidateQueries({ queryKey: ['setup-instruments'] })
        void savedSetupQuery.refetch().then(({ data }) => {
          if (!data) return
          setForm((current) => ({
            ...current,
            token: '',
            accountId: '',
            accountTypeCode: '',
            accountConfirmed: false,
            liveConfirmed: false,
            environment: data.environment,
            instruments: data.instruments.map(normalizeSetupInstrument),
          }))
          setAccounts([])
          setSelectedAccountId('')
        })
      } else {
        void strategiesQuery.refetch()
      }
    },
  })
  const instrumentsQuery = useQuery({
    queryKey: ['setup-instruments', form.environment, form.token, selectedAccountId, savedSetupQuery.data?.configured],
    queryFn: () => getSetupInstruments({
      ...(form.token.trim() && selectedAccountId
        ? { token: form.token, accountId: selectedAccountId }
        : {}),
      environment: form.environment,
    }),
    enabled: Boolean(
      (form.token.trim() && selectedAccountId)
      || (savedSetupQuery.data?.configured && savedSetupQuery.data.environment === form.environment),
    ),
    retry: false,
  })

  useEffect(() => {
    setSelectedInstruments(form.instruments.map((item) => item.replace(/_/g, '/')))
  }, [form.instruments, setSelectedInstruments])

  useEffect(() => {
    const saved = savedSetupQuery.data
    if (!saved || setupBaselineLoaded.current) return
    setupBaselineLoaded.current = true
    setForm((current) => ({
      ...current,
      environment: saved.configured ? saved.environment : current.environment,
      instruments: saved.configured ? saved.instruments.map(normalizeSetupInstrument) : [],
    }))
  }, [savedSetupQuery.data])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null)
    setSaveMessage(null)
    if (canSavePairsOnly) {
      try {
        const result = await instrumentsMutation.mutateAsync(form.instruments.map(normalizeSetupInstrument))
        const saved = savedSetupQuery.data
        if (!saved?.configured) throw new Error('Pair save completed, but the active setup is unavailable.')
        const normalizedInstruments = result.instruments.map(normalizeSetupInstrument)
        const updatedSetup = { ...saved, environment: result.environment, instruments: normalizedInstruments }
        setForm((current) => ({ ...current, instruments: normalizedInstruments }))
        setSelectedInstruments(normalizedInstruments.map((item) => item.replace(/_/g, '/')))
        queryClient.setQueryData<SetupStatus>(['setup-status'], updatedSetup)
        setSaveMessage(`✅ ${normalizedInstruments.length} trading pair${normalizedInstruments.length === 1 ? '' : 's'} saved to the bot configuration.`)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : 'Trading pairs could not be saved')
      }
      return
    }
    if (
      startAfterSave &&
      form.environment === 'live' &&
      !window.confirm('Start automatic execution on OANDA Live? Approved signals can place real orders and close real positions.')
    ) return
    let saved: SetupStatus
    try {
      saved = await mutation.mutateAsync(form)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Setup was rejected')
      return
    }
    setSelectedInstruments(form.instruments.map((item) => item.replace(/_/g, '/')))
    queryClient.setQueryData<SetupStatus>(['setup-status'], saved)
    setSaveMessage('Setup settings were saved successfully.')
    if (startAfterSave) {
      try {
        const status = await startExecutionMutation.mutateAsync()
        if (!status.enabled) {
          throw new Error('The server did not confirm that automatic execution was enabled.')
        }
        setSaveMessage(
          `Setup saved. Automatic ${form.environment} strategy execution is enabled; waiting for the first strategy cycle.`,
        )
        void autoExecutionQuery.refetch()
      } catch (reason) {
        void autoExecutionQuery.refetch()
        setError(
          `Setup was saved, but automatic execution did not start: ${
            reason instanceof Error ? reason.message : 'request rejected'
          }`,
        )
        return
      }
    }
  }

  const addInstrument = (instrument: string) => {
    setForm((current) => {
      if (current.instruments.includes(instrument)) return current
      return {
        ...current,
        instruments: [...current.instruments, instrument],
      }
    })
  }

  const removeInstrument = (instrument: string) => {
    setForm((current) => {
      if (!current.instruments.includes(instrument)) return current
      return {
        ...current,
        instruments: current.instruments.filter((item) => item !== instrument),
      }
    })
  }

  const instrumentGroups = useMemo(() => {
    const instruments = instrumentsQuery.data?.instruments ?? []
    const search = instrumentSearch.trim().toLowerCase()
    const groups = new Map<string, typeof instruments>()
    for (const instrument of instruments) {
      if (search && !`${instrument.name} ${instrument.displayName}`.toLowerCase().includes(search)) continue
      const currencies = instrument.name.split('_')
      const group = currencies.length === 2 && currencies.every((currency) => currency.length === 3)
        ? currencies.includes('USD') ? 'USD pairs' : 'Currency crosses'
        : 'Other instruments'
      groups.set(group, [...(groups.get(group) ?? []), instrument])
    }
    return ['USD pairs', 'Currency crosses', 'Other instruments']
      .flatMap((name) => {
        const items = groups.get(name)
        return items?.length ? [{ name, items }] : []
      })
  }, [instrumentSearch, instrumentsQuery.data?.instruments])

  const hasSetupChanges = useMemo(() => {
    const saved = savedSetupQuery.data
    if (!saved) return false
    const currentInstruments = form.instruments.map(normalizeSetupInstrument).sort()
    const savedInstruments = (saved.configured ? saved.instruments : []).map(normalizeSetupInstrument).sort()
    return form.environment !== saved.environment
      || currentInstruments.join(',') !== savedInstruments.join(',')
      || Boolean(form.token.trim() && form.accountConfirmed)
  }, [form.accountConfirmed, form.environment, form.instruments, form.token, savedSetupQuery.data])
  const hasPairChanges = useMemo(() => {
    const saved = savedSetupQuery.data
    if (!saved?.configured) return false
    const currentInstruments = form.instruments.map(normalizeSetupInstrument).sort()
    const savedInstruments = saved.instruments.map(normalizeSetupInstrument).sort()
    return currentInstruments.join(',') !== savedInstruments.join(',')
  }, [form.instruments, savedSetupQuery.data])
  const canSavePairsOnly = Boolean(
    savedSetupQuery.data?.configured
    && form.environment === savedSetupQuery.data.environment
    && hasPairChanges
    && !form.token.trim()
    && !selectedAccountId
    && !startAfterSave,
  )

  const runRuntimeAction = (action: 'reload' | 'resume' | 'pause' | 'stop') => {
    if (action === 'stop' && !window.confirm('Stop the bot completely? New trades and open-trade management will both be disabled.')) return
    setOperationMessage(null)
    runtimeMutation.mutate(action)
  }
  const runServiceAction = (action: SystemdServiceAction) => {
    if (action === 'stop' || action === 'disable-now') {
      if (!window.confirm('Stop the systemd service? The UI connection will be interrupted.')) return
    }
    if (action === 'restart') {
      if (!window.confirm('Restart the systemd service? The UI connection will be interrupted briefly.')) return
    }
    serviceMutation.mutate(action)
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
      } catch (reason) {
        setStrategyUploadMessage(reason instanceof Error ? reason.message : `${file.name} was rejected`)
        break
      }
    }
  }
  const selectedAccount = accounts.find((account) => account.accountId === selectedAccountId)
  const serviceStatusLabel = serviceQuery.data?.activeState === 'active'
    ? 'RUNNING'
    : serviceQuery.data?.activeState === 'activating'
      ? 'STARTING'
      : serviceQuery.data?.activeState === 'failed'
        ? 'FAILED'
        : serviceQuery.data?.activeState === 'inactive'
          ? 'STOPPED'
          : serviceQuery.data?.loadState === 'not-found'
            ? 'NOT INSTALLED'
            : serviceQuery.isError
              ? 'UNAVAILABLE'
              : 'CHECKING'
  const serviceStatusTone = serviceQuery.data?.activeState === 'active'
    ? 'online'
    : serviceQuery.data?.activeState === 'failed'
      ? 'failed'
      : serviceQuery.data?.loadState === 'not-found'
        ? 'missing'
        : 'idle'
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
            {form.environment === 'live' && <p className="setup-live-note field-wide">Live uses the real OANDA API and can place orders against real funds. Saving and starting require the account confirmation below, an explicit start confirmation, and an admin session. Test using Practice first.</p>}
            <label className="field-block field-wide">
              <span>OANDA {form.environment === 'live' ? 'Live' : 'Practice'} token</span>
              <input type="password" autoComplete="new-password" required={!canSavePairsOnly} value={form.token} onChange={(event) => { discoveryMutation.reset(); setAccounts([]); setSelectedAccountId(''); setForm((current) => ({ ...current, token: event.target.value, accountId: '', accountTypeCode: '', accountConfirmed: false, liveConfirmed: false })) }} />
              <small>
                {savedSetupQuery.data?.configured
                  ? 'Leave blank when saving pair changes only; the server will securely use the saved OANDA account.'
                  : 'Sent to OANDA only for read-only account discovery. Saved on this server only after confirmation.'}
              </small>
            </label>
            <div className="field-wide discovery-action"><button type="button" className="primary-action" onClick={discoverAccounts} disabled={!form.token.trim() || discoveryMutation.isPending}>{discoveryMutation.isPending ? 'Checking OANDA...' : 'Discover accounts'}</button>{discoveryMutation.isPending && <span>Reading account types and summaries. No trading request will be sent.</span>}</div>

            {discoveryMutation.isSuccess && <section className="account-discovery field-wide" aria-live="polite"><div className="account-discovery-heading"><div><strong>{accounts.length ? 'Supported accounts found' : 'No supported accounts found'}</strong><small>{accounts.length} eligible · {discoveryMutation.data.excludedAccountCount} other account(s) excluded</small></div><span>READ ONLY</span></div>{accounts.length > 0 && <div className="account-choice-list">{accounts.map((account) => { const summary = account.summary; const selected = account.accountId === selectedAccountId; return <button type="button" key={account.accountId} className={`account-choice ${selected ? 'selected' : ''} ${!account.summaryAccessible ? 'unavailable' : ''}`} aria-pressed={selected} disabled={!account.summaryAccessible} onClick={() => selectAccount(account)}><span className="account-choice-radio" aria-hidden="true"/><span className="account-choice-main"><span className="account-choice-title">{summary?.alias || 'OANDA account'}<span className={`account-type-code code-${account.accountTypeCode}`}>{account.accountTypeCode} · {account.accountType}</span></span><span className="account-choice-id">{account.accountId}</span><span className="account-choice-meta">{summary?.currency || 'Currency unavailable'}{summary?.NAV ? ` · NAV ${summary.NAV}` : ''}{summary?.marginAvailable ? ` · Margin available ${summary.marginAvailable}` : ''}</span>{!account.summaryAccessible && <span className="account-choice-warning">Account summary could not be read; this account cannot be selected.</span>}</span></button> })}</div>}</section>}

            {selectedAccount && <label className="account-confirm field-wide"><input type="checkbox" checked={form.accountConfirmed} onChange={(event) => setForm((current) => ({ ...current, accountConfirmed: event.target.checked }))}/><span>I confirm account {selectedAccount.accountId} is the {selectedAccount.accountType} ({selectedAccount.accountTypeCode}) account I want to connect in {form.environment.toUpperCase()}.</span></label>}
            {selectedAccount && form.environment === 'live' && <label className="account-confirm live-confirm field-wide"><input type="checkbox" checked={form.liveConfirmed} onChange={(event) => setForm((current) => ({ ...current, liveConfirmed: event.target.checked }))}/><span>This is a real Live account. I understand that saving Live mode routes order execution to OANDA Live and can affect real funds.</span></label>}

            <div className="setup-section-heading"><span>02</span><div><strong>Market defaults</strong><small>These settings apply to the selected Practice or Live account.</small></div></div>
            <div className="field-block field-wide">
              <div className="instrument-picker-heading">
                <span>Choose currency pairs</span>
                <span className="instrument-count">{form.instruments.length} selected</span>
              </div>
              {instrumentsQuery.isLoading && <small>Loading all available OANDA instruments...</small>}
              {instrumentsQuery.isError && <small className="field-error">{instrumentsQuery.error instanceof Error ? instrumentsQuery.error.message : 'Unable to load the instrument list.'}</small>}
              {instrumentsQuery.data && instrumentsQuery.data.instruments.length > 0 && (
                <label className="instrument-search">
                  <span aria-hidden="true">🔎</span>
                  <input
                    type="search"
                    value={instrumentSearch}
                    onChange={(event) => setInstrumentSearch(event.target.value)}
                    placeholder="Search pairs, e.g. EUR or JPY"
                    aria-label="Search available instruments"
                  />
                </label>
              )}
              <div className="instrument-groups" aria-live="polite">
                {instrumentGroups.map(({ name, items }) => {
                  const selectedCount = items.filter((instrument) => form.instruments.includes(instrument.name)).length
                  return (
                    <details className="instrument-group" key={name} open>
                      <summary>
                        <span className="instrument-group-title">{name}</span>
                        <span className="instrument-group-count">{selectedCount} / {items.length} selected</span>
                        <span className="instrument-expand" aria-hidden="true">⌄</span>
                      </summary>
                      <div className="instrument-selector">
                        {items.map((instrument) => {
                          const selected = form.instruments.includes(instrument.name)
                          return (
                            <button
                              type="button"
                              key={instrument.name}
                              className={`instrument-option ${selected ? 'selected' : ''} priority-${instrument.priority}`}
                              onClick={() => (selected ? removeInstrument(instrument.name) : addInstrument(instrument.name))}
                              aria-pressed={selected}
                            >
                              <span className="instrument-option-symbol">
                                <span>{selected ? '✓' : '+'}</span>
                                {instrument.displayName}
                              </span>
                              <small>{instrument.priority === 'high' ? '⭐ Popular' : instrument.priority === 'medium' ? '👀 Watchlist' : '＋ Available'}</small>
                            </button>
                          )
                        })}
                      </div>
                    </details>
                  )
                })}
                {instrumentsQuery.data && instrumentsQuery.data.instruments.length === 0 && <small>No OANDA instruments were returned for this account.</small>}
                {instrumentsQuery.data && instrumentGroups.length === 0 && instrumentsQuery.data.instruments.length > 0 && <small>No pairs match your search.</small>}
                {!instrumentsQuery.data && !instrumentsQuery.isLoading && !instrumentsQuery.isError && <small>Connect an OANDA account to load the live instrument list.</small>}
              </div>
              <small>Click a pair to add or remove it. Selected pairs are highlighted in green and are saved to the bot config when you save.</small>
            </div>

            <div className="setup-section-heading"><span>03</span><div><strong>Selected pairs</strong><small>Review or remove pairs before saving your bot configuration.</small></div></div>
            <ul className="setup-pair-list field-wide">
              {form.instruments.map((instrument) => (
                <li className="setup-pair-item" key={instrument}>
                  <span>💱 {instrument.replace(/_/g, '/')}</span>
                  <button type="button" onClick={() => removeInstrument(instrument)} aria-label={`Remove ${instrument.replace(/_/g, '/')} from selected pairs`}>Remove</button>
                </li>
              ))}
              {form.instruments.length === 0 && <li className="setup-pairs-empty">No pairs selected yet. Choose at least one pair above.</li>}
            </ul>
            <div className="setup-section-heading"><span>04</span><div><strong>Dashboard layout</strong><small>Choose which account and execution details appear on the dashboard.</small></div></div>
            <label className="account-confirm dashboard-preference field-wide">
              <input
                type="checkbox"
                checked={savedSetupQuery.data?.showPortfolioCockpit ?? false}
                disabled={savedSetupQuery.isLoading || dashboardPreferenceMutation.isPending}
                onChange={(event) => dashboardPreferenceMutation.mutate(event.target.checked)}
              />
              <span>
                <strong>Show Account &amp; execution · Portfolio cockpit</strong>
                <small>
                  Display the portfolio cockpit on the dashboard, including account snapshot,
                  exposure, broker positions, and manual order controls. This preference is saved
                  immediately and does not require reconnecting your OANDA account.
                </small>
                {dashboardPreferenceMutation.isPending && <small>Saving dashboard preference…</small>}
                {savedSetupQuery.isError && (
                  <small className="setup-error" role="alert">
                    {savedSetupQuery.error instanceof Error
                      ? savedSetupQuery.error.message
                      : 'Dashboard preference could not be loaded.'}
                  </small>
                )}
                {dashboardPreferenceMutation.isError && (
                  <small className="setup-error" role="alert">
                    {dashboardPreferenceMutation.error instanceof Error
                      ? dashboardPreferenceMutation.error.message
                      : 'Dashboard preference could not be saved.'}
                  </small>
                )}
              </span>
            </label>
            <label className="account-confirm field-wide">
              <input
                type="checkbox"
                checked={startAfterSave}
                disabled={form.environment === 'live' && userRole !== 'admin'}
                onChange={(event) => setStartAfterSave(event.target.checked)}
              />
              <span>
                Start automatic strategy execution immediately after saving. The server will
                require an approved strategy revision and complete risk controls for every pair.
                {form.environment === 'live' && userRole !== 'admin'
                  ? ' Live execution can only be started from an admin session.'
                  : ''}
              </span>
            </label>

            <section className="setup-auto-status field-wide" aria-labelledby="setup-auto-status-title">
              <strong id="setup-auto-status-title">Automatic strategy execution</strong>
              {autoExecutionQuery.isLoading && <small>Checking execution status…</small>}
              {autoExecutionQuery.isError && (
                <small className="setup-error" role="alert">
                  {autoExecutionQuery.error instanceof Error
                    ? autoExecutionQuery.error.message
                    : 'Automatic execution status is unavailable.'}
                </small>
              )}
              {autoExecutionQuery.data && (
                <>
                  <span>
                    {autoExecutionQuery.data.enabled
                      ? `Active · ${autoExecutionQuery.data.environment}${autoExecutionQuery.data.workerRunning ? '' : ' · Worker stopped'}`
                      : 'Not active'}
                  </span>
                  {autoExecutionQuery.data.lastCycleAt && (
                    <small>Last strategy cycle: {new Date(autoExecutionQuery.data.lastCycleAt).toLocaleString()}</small>
                  )}
                  {autoExecutionQuery.data.lastError && (
                    <small className="setup-error" role="alert">
                      {autoExecutionQuery.data.enabled
                        ? `Execution is enabled, but the latest cycle reported an error: ${autoExecutionQuery.data.lastError}`
                        : `Automatic execution did not start: ${autoExecutionQuery.data.lastError}`}
                    </small>
                  )}
                </>
              )}
              <a href="/risk" target="_blank" rel="noreferrer">
                Review risk controls for every configured pair
              </a>
            </section>

            {error && <p className="setup-error" role="alert">{error}</p>}
            {saveMessage && <p className="setup-operation-message field-wide" role="status">{saveMessage}</p>}
            <div className="setup-footer">
              <span className="setup-secure-note">{form.environment === 'live' ? 'Live account · server-side config' : 'Practice environment · server-side config'}</span>
              <button className="primary-action setup-submit" type="submit" disabled={savedSetupQuery.isLoading || !hasSetupChanges || mutation.isPending || instrumentsMutation.isPending || startExecutionMutation.isPending || (!canSavePairsOnly && (!selectedAccount || !form.accountConfirmed || (form.environment === 'live' && (!form.liveConfirmed || userRole !== 'admin')) || (startAfterSave && form.environment === 'live' && userRole !== 'admin'))) || form.instruments.length === 0}>
                {instrumentsMutation.isPending ? 'Saving trading pairs…' : mutation.isPending ? 'Saving configuration...' : startExecutionMutation.isPending ? 'Starting strategy execution...' : startAfterSave ? `Save and start ${form.environment === 'live' ? 'Live' : 'Practice'} trading` : `Save ${form.environment === 'live' ? 'Live' : 'Practice'} mode`}
              </button>
            </div>
          </form>

          <section className="setup-operations" aria-labelledby="operations-title">
            <div className="setup-section-heading"><span>05</span><div><strong id="operations-title">Operations</strong><small>Apply file changes and control the bot without leaving setup.</small></div></div>
            <div className="setup-file-grid">
              <section className="setup-file-card strategy-file-card" aria-labelledby="strategy-files-title">
                <div className="setup-file-heading">
                  <span className="setup-file-icon" aria-hidden="true">🧠</span>
                  <div><strong id="strategy-files-title">Strategy files</strong><small>Upload validated Freqtrade Python strategies. Strategy class names must be unique.</small></div>
                </div>
                <div className="setup-file-actions">
                  <label className="secondary-action">{uploadMutation.isPending ? 'Uploading…' : '⬆️ Upload strategies'}<input type="file" accept=".py,text/x-python" multiple disabled={uploadMutation.isPending} onChange={handleStrategyFiles} /></label>
                  <a className="secondary-action" href={getSetupFileUrl('strategy')} download>⬇️ Download active</a>
                </div>
                {strategiesQuery.isLoading && <small>Loading strategy catalog…</small>}
                {strategiesQuery.isError && <small className="field-error" role="alert">⚠️ {strategiesQuery.error.message}</small>}
                {strategyUploadMessage && <small className="setup-file-status" role="status">✅ {strategyUploadMessage}</small>}
                {strategiesQuery.data && strategiesQuery.data.length > 0
                  ? <ul className="setup-strategy-list">{strategiesQuery.data.map((strategy) => <li key={strategy.name}><span>📄 <strong>{strategy.name}</strong><small>{strategy.fileName}{strategy.builtin ? ' · Built-in' : ' · Uploaded'}</small></span><a href={getSetupFileUrl('strategy', strategy.name)} download aria-label={`Download ${strategy.name}`}>Download</a></li>)}</ul>
                  : strategiesQuery.data && <small>No strategy files are currently available.</small>}
              </section>
              <section className="setup-file-card config-file-card" aria-labelledby="config-file-title">
                <div className="setup-file-heading">
                  <span className="setup-file-icon config" aria-hidden="true">⚙️</span>
                  <div><strong id="config-file-title">config.json</strong><small>Import a validated JSON config or download the active bot configuration.</small></div>
                </div>
                <div className="setup-config-meta">
                  <span className={`config-state-dot ${savedSetupQuery.data?.configured ? 'configured' : ''}`} />
                  {savedSetupQuery.isLoading ? 'Checking active configuration…' : savedSetupQuery.data?.configured ? `Active · ${savedSetupQuery.data.environment.toUpperCase()}` : 'No OANDA setup saved'}
                </div>
                <div className="setup-file-actions">
                  <label className="secondary-action">{uploadMutation.isPending ? 'Uploading…' : '⬆️ Upload config'}<input type="file" accept="application/json,.json" disabled={uploadMutation.isPending} onChange={(event) => { setConfigUploadMessage(null); handleFile('config', event) }} /></label>
                  <a className="secondary-action" href={getSetupFileUrl('config')} download>⬇️ Download config.json</a>
                </div>
                {configUploadMessage && <small className="setup-file-status" role="status">{configUploadMessage}</small>}
                {uploadMutation.isError && uploadMutation.variables?.kind === 'config' && <small className="field-error" role="alert">⚠️ {uploadMutation.error.message}</small>}
                {savedSetupQuery.isError && <small className="field-error" role="alert">⚠️ Active configuration status could not be loaded: {savedSetupQuery.error.message}</small>}
                <small className="setup-config-note">🔒 Live approval is never imported from a file. Confirm Live mode in Setup before trading.</small>
              </section>
            </div>
            <div className="runtime-control-card">
              <div><strong>Bot runtime</strong><small>{runtimeQuery.data?.message ?? 'Checking runtime state...'}</small></div>
              <div className="runtime-actions"><button type="button" className="secondary-action" onClick={() => runRuntimeAction('reload')} disabled={runtimeMutation.isPending}>Reload</button>{runtimeQuery.data?.state === 'paused' || runtimeQuery.data?.state === 'stopped' ? <button type="button" className="secondary-action" onClick={() => runRuntimeAction('resume')} disabled={runtimeMutation.isPending}>Resume</button> : <button type="button" className="secondary-action" onClick={() => runRuntimeAction('pause')} disabled={runtimeMutation.isPending}>Pause</button>}<button type="button" className="danger-action" onClick={() => runRuntimeAction('stop')} disabled={runtimeMutation.isPending}>Stop bot</button></div>
            </div>
            <div className="systemd-control-card">
              <div className="systemd-control-heading">
                <div className="systemd-service-identity">
                  <span className="systemd-service-icon" aria-hidden="true">⚙️</span>
                  <div>
                    <strong>Linux systemd service</strong>
                    <small>Manage the background process · freqtrade-forex</small>
                  </div>
                </div>
                {userRole === 'admin' && (
                  <div className={`systemd-status-badge ${serviceStatusTone}`}>
                    <span aria-hidden="true" />
                    {serviceStatusLabel}
                  </div>
                )}
              </div>
              {userRole !== 'admin' && (
                <p className="systemd-setup-note">
                  🔒 Administrator role required to view service status, logs, or controls.
                </p>
              )}
              {userRole === 'admin' && serviceQuery.data?.supported === false && (
                <p className="systemd-setup-note">ℹ️ {serviceQuery.data.message}</p>
              )}
              {userRole === 'admin' && serviceQuery.isError && (
                <p className="systemd-setup-note systemd-error-note" role="alert">
                  ⚠️ {serviceQuery.error.message}
                </p>
              )}
              {userRole === 'admin' && serviceQuery.data?.supported && serviceQuery.data.loadState !== 'not-found' && (
                <>
                  <div className="systemd-service-meta">
                    <span>⚡ {serviceQuery.data.subState ?? serviceQuery.data.activeState ?? 'State unknown'}</span>
                    <span>🚀 Startup: {serviceQuery.data.unitFileState ?? 'unknown'}</span>
                    <button
                      type="button"
                      className="systemd-refresh-button"
                      onClick={() => void serviceQuery.refetch()}
                      disabled={serviceQuery.isFetching || serviceMutation.isPending}
                    >
                      {serviceQuery.isFetching ? '⏳ Checking…' : '🔄 Refresh status & logs'}
                    </button>
                  </div>
                  <div className="systemd-action-groups">
                    <section className="systemd-action-group" aria-label="Service controls">
                      <h4>🧰 Service controls</h4>
                      <div className="systemd-button-grid">
                        <button type="button" className="systemd-action-button" onClick={() => runServiceAction('daemon-reload')} disabled={serviceMutation.isPending}>🗂️ Reload unit files</button>
                        <button type="button" className="systemd-action-button" onClick={() => runServiceAction('start')} disabled={serviceMutation.isPending}>▶️ Start service</button>
                        <button type="button" className="systemd-action-button" onClick={() => runServiceAction('restart')} disabled={serviceMutation.isPending}>🔄 Restart service</button>
                        <button type="button" className="systemd-action-button danger" onClick={() => runServiceAction('stop')} disabled={serviceMutation.isPending}>🛑 Stop service</button>
                      </div>
                    </section>
                    <section className="systemd-action-group" aria-label="Startup controls">
                      <h4>🌅 Start-up behavior</h4>
                      <div className="systemd-button-grid">
                        <button type="button" className="systemd-action-button highlight" onClick={() => runServiceAction('enable-now')} disabled={serviceMutation.isPending}>🚀 Enable + start now</button>
                        <button type="button" className="systemd-action-button" onClick={() => runServiceAction('enable')} disabled={serviceMutation.isPending}>🔁 Enable autostart</button>
                        <button type="button" className="systemd-action-button" onClick={() => runServiceAction('disable')} disabled={serviceMutation.isPending}>🌙 Disable autostart</button>
                        <button type="button" className="systemd-action-button danger" onClick={() => runServiceAction('disable-now')} disabled={serviceMutation.isPending}>⏹️ Disable + stop</button>
                      </div>
                    </section>
                  </div>
                </>
              )}
              {userRole === 'admin' && serviceQuery.data?.supported && serviceQuery.data.loadState === 'not-found' && (
                <p className="systemd-setup-note">
                  🛠️ The service unit is not installed yet. Complete the one-time setup from a terminal, then refresh this page.
                </p>
              )}
              {userRole === 'admin' && serviceQuery.data?.supported && serviceQuery.data.logs.length > 0 && (
                <details className="systemd-log-details" open>
                  <summary>📜 Recent service logs <span>{serviceQuery.data.logs.length} lines</span></summary>
                  <pre className="systemd-log" aria-label="Recent systemd service logs">{serviceQuery.data.logs.join('\n')}</pre>
                </details>
              )}
              {userRole === 'admin' && serviceQuery.data?.supported && serviceQuery.data.logs.length === 0 && serviceQuery.data.loadState !== 'not-found' && (
                <p className="systemd-setup-note">📭 No recent service journal entries.</p>
              )}
              {serviceMutation.isSuccess && <p className="setup-operation-message" role="status">✨ {serviceMutation.data.message}</p>}
              {serviceMutation.isError && <p className="setup-error" role="alert">⚠️ {serviceMutation.error.message}</p>}
            </div>
            {operationMessage && <p className="setup-operation-message" role="status">{operationMessage}</p>}
            {uploadMutation.isError && <p className="setup-error" role="alert">{uploadMutation.error instanceof Error ? uploadMutation.error.message : 'File upload was rejected'}</p>}
          </section>
        </div>
      </section>
    </main>
  )
}
