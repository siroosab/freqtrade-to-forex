import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { getSettings } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

export function SettingsPage() {
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings })
  const environment = useUiStore((state) => state.environment)
  const setEnvironment = useUiStore((state) => state.setEnvironment)
  const alerts = useUiStore((state) => state.alerts)
  const executionMode = useUiStore((state) => state.executionMode)
  const connectionState = useUiStore((state) => state.connectionState)

  const monitorCards = useMemo(
    () => [
      { label: 'Environment', value: environment.toUpperCase() },
      { label: 'Execution mode', value: executionMode },
      { label: 'Broker', value: data?.broker ?? 'OANDA' },
      { label: 'Socket', value: connectionState },
    ],
    [connectionState, data?.broker, environment, executionMode],
  )

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
