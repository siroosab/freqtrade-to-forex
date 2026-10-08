import { useEffect, useState } from 'react'
import { getServerMetrics, type ServerMetrics } from '../api/mockApi'
import { ServerTime } from './ServerTime'

function formatMemory(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

function UsageMetric({
  label,
  value,
  detail,
}: {
  label: string
  value: number
  detail?: string
}) {
  const percentage = Math.min(100, Math.max(0, value))

  return (
    <div className="server-metric">
      <div className="server-metric-heading">
        <span>{label}</span>
        <strong>{Math.round(value)}%</strong>
      </div>
      <div
        className="server-metric-track"
        role="meter"
        aria-label={`${label} usage`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percentage)}
      >
        <span style={{ width: `${percentage}%` }} />
      </div>
      {detail && <small>{detail}</small>}
    </div>
  )
}

export function ServerStatusPanel({
  onTimestampChange,
}: {
  onTimestampChange: (timestamp: string) => void
}) {
  const [metrics, setMetrics] = useState<ServerMetrics | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    let active = true
    let timer: number | undefined

    const refresh = async () => {
      try {
        const latest = await getServerMetrics()
        if (active) {
          setMetrics(latest)
          onTimestampChange(latest.timestamp)
          setUnavailable(false)
        }
      } catch {
        if (active) setUnavailable(true)
      } finally {
        if (active) timer = window.setTimeout(() => void refresh(), 3000)
      }
    }

    void refresh()
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [onTimestampChange])

  return (
    <section className={`server-panel${unavailable ? ' unavailable' : ''}`} aria-label="Server status">
      <div className="server-panel-heading">
        <p className="eyebrow">Server Status</p>
        <span className="server-status-indicator" aria-hidden="true" />
      </div>
      {metrics ? (
        <>
          <UsageMetric label="CPU" value={metrics.cpuPercent} />
          <UsageMetric
            label="RAM"
            value={metrics.memoryPercent}
            detail={`${formatMemory(metrics.memoryUsed)} / ${formatMemory(metrics.memoryTotal)}`}
          />
          <ServerTime key={metrics.timestamp} timestamp={metrics.timestamp} />
          <p className="server-panel-footnote">
            {unavailable ? 'Connection lost · showing last update' : 'Live · updates every 3s'}
          </p>
        </>
      ) : (
        <p className="server-panel-message">
          {unavailable ? 'Server metrics unavailable' : 'Loading server metrics...'}
        </p>
      )}
    </section>
  )
}
