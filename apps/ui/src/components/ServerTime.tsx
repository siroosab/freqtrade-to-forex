import { useEffect, useState } from 'react'

const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

const timeFormatter = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
  timeZone: 'UTC',
})

export function ServerTime({
  timestamp,
  variant = 'panel',
}: {
  timestamp: string | null
  variant?: 'brand' | 'panel'
}) {
  const [clock, setClock] = useState(() => {
    const now = Date.now()
    return { receivedAt: now, now }
  })

  useEffect(() => {
    const timer = window.setInterval(() => {
      setClock((current) => ({ ...current, now: Date.now() }))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])

  const timestampMs = timestamp ? Date.parse(timestamp) : Number.NaN
  const currentServerTime = timestampMs + clock.now - clock.receivedAt
  const date = new Date(currentServerTime)
  const hasValidTime = Number.isFinite(currentServerTime)

  return (
    <div className={`server-time server-time-${variant}`}>
      <span className="server-time-label">Server time (UTC)</span>
      {hasValidTime ? (
        <time className="server-time-value" dateTime={date.toISOString()}>
          <span>{dateFormatter.format(date)}</span>
          <strong>{timeFormatter.format(date)}</strong>
        </time>
      ) : (
        <span className="server-time-value">Waiting for server time...</span>
      )}
    </div>
  )
}
