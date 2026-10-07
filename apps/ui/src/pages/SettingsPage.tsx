import { useQuery } from '@tanstack/react-query'
import { getServerExecutionStatus, getSettings } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

export function SettingsPage() {
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings })
  const executionStatus = useQuery({
    queryKey: ['server-execution-status'],
    queryFn: getServerExecutionStatus,
    refetchInterval: 15_000,
    retry: false,
  })
  const alerts = useUiStore((state) => state.alerts)
  const connectionState = useUiStore((state) => state.connectionState)

  const executionMode = executionStatus.data?.executionMode
    .replace('_', '-')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
  const isLiveConfiguration = executionStatus.data?.environment === 'live'
    || executionStatus.data?.executionMode === 'live'
  const monitorCards = [
    { label: 'Environment', value: executionStatus.data?.environment.toUpperCase() ?? 'Unavailable' },
    { label: 'Execution mode', value: executionMode ?? 'Unavailable' },
    { label: 'Broker', value: data?.broker ?? 'OANDA' },
    { label: 'Socket', value: connectionState },
  ]

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
            <h3>Server execution configuration</h3>
          </div>
          <span className={`pill ${isLiveConfiguration ? 'negative' : 'positive'}`}>
            {executionStatus.isError
              ? 'Server status unavailable'
              : isLiveConfiguration
                ? 'Live configuration'
                : executionStatus.data
                  ? 'Practice configuration'
                  : 'Loading configuration'}
          </span>
        </div>
        <div className="settings-grid">
          <div className="field-block muted-block">
            <span>Server environment (read-only)</span>
            <strong>{executionStatus.data?.environment.toUpperCase() ?? 'Unavailable'}</strong>
            <small>
              {executionStatus.data
                ? `Source: ${executionStatus.data.environmentSource}`
                : executionStatus.isLoading
                  ? 'Loading server configuration...'
                  : 'Unable to read server configuration'}
            </small>
          </div>

          <div className="field-block muted-block">
            <span>Server execution mode (read-only)</span>
            <strong>{executionMode ?? 'Unavailable'}</strong>
            <small>
              {executionStatus.data
                ? `Source: ${executionStatus.data.executionModeSource}`
                : executionStatus.isLoading
                  ? 'Loading server configuration...'
                  : 'Unable to read server configuration'}
            </small>
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
            <strong>Read-only server configuration</strong>
            <p>These values are resolved from the backend environment and configuration file. This screen does not change the bot's execution mode.</p>
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
