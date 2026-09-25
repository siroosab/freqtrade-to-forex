import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { getAiConfig, getSettings, saveAiConfig, type AiConfig } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

export function SettingsPage() {
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings })
  const aiQuery = useQuery({ queryKey: ['ai-config', 'EUR/USD'], queryFn: () => getAiConfig('EUR/USD') })
  const saveMutation = useMutation({ mutationFn: (config: AiConfig) => saveAiConfig(config, 'EUR/USD') })
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null)

  const environment = useUiStore((state) => state.environment)
  const setEnvironment = useUiStore((state) => state.setEnvironment)
  const alerts = useUiStore((state) => state.alerts)
  const executionMode = useUiStore((state) => state.executionMode)
  const connectionState = useUiStore((state) => state.connectionState)

  const [aiForm, setAiForm] = useState<AiConfig>({
    strategyName: 'FX Trend Pulse',
    model: 'hybrid',
    timeframe: 'M5',
    riskBudget: '0.72%',
    trainingMode: 'dry-run',
    featureSet: ['trend', 'spread', 'session', 'volatility'],
    entryThreshold: '0.5',
    exitThreshold: '0.0',
    volatilityWindow: '5',
    atrWindow: '14',
    maxSpreadPct: '1.0',
  })

  useEffect(() => {
    if (aiQuery.data) {
      setAiForm({
        strategyName: aiQuery.data.strategyName,
        model: aiQuery.data.model,
        timeframe: aiQuery.data.timeframe,
        riskBudget: aiQuery.data.riskBudget,
        trainingMode: aiQuery.data.trainingMode,
        featureSet: aiQuery.data.featureSet,
        entryThreshold: aiQuery.data.entryThreshold,
        exitThreshold: aiQuery.data.exitThreshold,
        volatilityWindow: aiQuery.data.volatilityWindow,
        atrWindow: aiQuery.data.atrWindow,
        maxSpreadPct: aiQuery.data.maxSpreadPct,
      })
    }
  }, [aiQuery.data])

  const monitorCards = useMemo(
    () => [
      { label: 'Environment', value: environment.toUpperCase() },
      { label: 'Execution mode', value: executionMode },
      { label: 'Broker', value: data?.broker ?? 'OANDA' },
      { label: 'Socket', value: connectionState },
    ],
    [connectionState, data?.broker, environment, executionMode],
  )

  const toggleFeature = (feature: string) => {
    setAiForm((current) => ({
      ...current,
      featureSet: current.featureSet.includes(feature)
        ? current.featureSet.filter((item) => item !== feature)
        : [...current.featureSet, feature],
    }))
  }

  const saveAiConfigForm = () => {
    setSaveFeedback(null)
    saveMutation.mutate(aiForm, {
      onSuccess: () => setSaveFeedback('AI configuration saved and accepted by the backend.'),
      onError: () => setSaveFeedback('AI configuration update was rejected by the backend.'),
    })
  }

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">CONTROL CENTER / SETTINGS</p>
          <h2>Environment and access</h2>
          <p className="settings-intro">Review the active broker connection and operating guardrails before changing strategy behavior.</p>
        </div>
        <span className={`status-pill ${connectionState === 'online' ? 'online' : 'neutral'}`}>{connectionState}</span>
      </header>

      <section className="panel page-panel">
        <div className="settings-section-heading">
          <div>
            <p className="eyebrow">Connection profile</p>
            <h3>Where the bot is operating</h3>
          </div>
          <span className="pill positive">Practice protected</span>
        </div>
        <div className="settings-grid">
          <label className="field-block">
            <span>Operational environment</span>
            <select value={environment} onChange={(event) => setEnvironment(event.target.value as typeof environment)}>
              <option value="dev">Dev</option>
              <option value="staging">Staging</option>
              <option value="practice">Practice</option>
              <option value="live">Live</option>
            </select>
          </label>

          <div className="field-block muted-block">
            <span>Current profile</span>
            <strong>{data?.environment ?? 'Practice'}</strong>
          </div>
        </div>

        <div className="summary-grid">
          {monitorCards.map((card) => (
            <div key={card.label} className="summary-card">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
            </div>
          ))}
        </div>
        <div className="settings-safety-note">
          <span className="setup-note-mark">!</span>
          <div>
            <strong>Live execution remains locked</strong>
            <p>Practice is the only broker execution path exposed during initial setup. A separate release approval is required before Live.</p>
          </div>
        </div>
      </section>

      <section className="panel page-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">AI model</p>
            <h3>Strategy tuning</h3>
          </div>
          <button className="primary-action" onClick={saveAiConfigForm} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? 'Saving…' : 'Save AI config'}
          </button>
        </div>

        {saveFeedback && (
          <div className="panel-header compact" style={{ marginBottom: '18px' }}>
            <span className="pill neutral">{saveFeedback}</span>
          </div>
        )}

        <div className="settings-grid">
          <label className="field-block">
            <span>Strategy name</span>
            <input value={aiForm.strategyName} onChange={(event) => setAiForm((current) => ({ ...current, strategyName: event.target.value }))} />
          </label>

          <label className="field-block">
            <span>Model type</span>
            <select value={aiForm.model} onChange={(event) => setAiForm((current) => ({ ...current, model: event.target.value as typeof aiForm.model }))}>
              <option value="rule-based">Rule-based</option>
              <option value="ml">ML</option>
              <option value="hybrid">Hybrid</option>
            </select>
          </label>

          <label className="field-block">
            <span>Timeframe</span>
            <select value={aiForm.timeframe} onChange={(event) => setAiForm((current) => ({ ...current, timeframe: event.target.value as typeof aiForm.timeframe }))}>
              <option value="M5">M5</option>
              <option value="M15">M15</option>
              <option value="H1">H1</option>
            </select>
          </label>

          <label className="field-block">
            <span>Risk budget</span>
            <input value={aiForm.riskBudget} onChange={(event) => setAiForm((current) => ({ ...current, riskBudget: event.target.value }))} />
          </label>

          <label className="field-block">
            <span>Training mode</span>
            <select value={aiForm.trainingMode} onChange={(event) => setAiForm((current) => ({ ...current, trainingMode: event.target.value as typeof aiForm.trainingMode }))}>
              <option value="dry-run">Dry-run</option>
              <option value="practice">Practice</option>
              <option value="backtest">Backtest</option>
            </select>
          </label>

          <label className="field-block">
            <span>Entry threshold</span>
            <input type="number" min="0.01" step="0.01" value={aiForm.entryThreshold} onChange={(event) => setAiForm((current) => ({ ...current, entryThreshold: event.target.value }))} />
          </label>

          <label className="field-block">
            <span>Exit threshold</span>
            <input type="number" min="0" step="0.01" value={aiForm.exitThreshold} onChange={(event) => setAiForm((current) => ({ ...current, exitThreshold: event.target.value }))} />
          </label>

          <label className="field-block">
            <span>Volatility window</span>
            <input type="number" min="2" step="1" value={aiForm.volatilityWindow} onChange={(event) => setAiForm((current) => ({ ...current, volatilityWindow: event.target.value }))} />
          </label>

          <label className="field-block">
            <span>ATR window</span>
            <input type="number" min="2" step="1" value={aiForm.atrWindow} onChange={(event) => setAiForm((current) => ({ ...current, atrWindow: event.target.value }))} />
          </label>

          <label className="field-block">
            <span>Max spread %</span>
            <input type="number" min="0.01" step="0.01" value={aiForm.maxSpreadPct} onChange={(event) => setAiForm((current) => ({ ...current, maxSpreadPct: event.target.value }))} />
          </label>
        </div>

        <div className="panel-header compact" style={{ marginTop: '20px' }}>
          <div>
            <p className="eyebrow">Features</p>
            <h3>Signal inputs</h3>
          </div>
        </div>

        <div className="summary-grid">
          {['trend', 'spread', 'session', 'volatility', 'risk', 'news'].map((feature) => (
            <button
              key={feature}
              type="button"
              className={`secondary-action ${aiForm.featureSet.includes(feature) ? 'selected' : ''}`}
              onClick={() => toggleFeature(feature)}
              style={{ textTransform: 'capitalize', justifyContent: 'center' }}
            >
              {feature}
            </button>
          ))}
        </div>
      </section>

      <section className="panel page-panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Monitor</p>
            <h3>Alert center</h3>
          </div>
        </div>

        <div className="alert-list compact-alerts">
          {alerts.map((alert) => (
            <div key={alert.id} className={`alert-item alert-level-${alert.level}`}>
              <span className={`alert-dot alert-${alert.level}`} />
              <div>
                <strong>{alert.title}</strong>
                <p>{alert.detail}</p>
              </div>
              <time>{new Date(alert.time).toLocaleTimeString()}</time>
            </div>
          ))}
        </div>
      </section>
    </>
  )
}
