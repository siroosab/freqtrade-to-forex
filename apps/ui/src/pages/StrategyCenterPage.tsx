import { useQuery } from '@tanstack/react-query'
import { getStrategySummary } from '../api/mockApi'

function readPercent(value: string | undefined) {
  const cleaned = Number.parseFloat((value ?? '0%').replace(/[^\d.-]/g, ''))
  return Number.isFinite(cleaned) ? cleaned : 0
}

export function StrategyCenterPage() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['strategies'], queryFn: getStrategySummary })

  const averageConfidence = data?.length
    ? `${Math.round(
        data.reduce((sum, item) => sum + readPercent(item.confidence), 0) / data.length,
      )}%`
    : '0%'

  const summary = [
    { label: 'Active', value: data?.filter((item) => item.enabled).length ?? 0 },
    { label: 'Signals', value: data?.filter((item) => item.signal.toLowerCase() !== 'neutral').length ?? 0 },
    { label: 'Avg confidence', value: averageConfidence },
    { label: 'Warmup', value: data?.[0]?.warmup ?? '—' },
  ]

  const decisionDetails = data?.slice(0, 3).map((strategy) => ({
    name: strategy.name,
    state: strategy.signal,
    detail: `${strategy.status} • ${strategy.mode} mode • confidence ${strategy.confidence}`,
    tone: strategy.signal.toLowerCase().includes('buy') ? 'success' : strategy.signal.toLowerCase().includes('short') ? 'warning' : 'neutral',
  })) ?? []

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Strategy Center</p>
          <h2>Operational strategy intelligence</h2>
        </div>
        <span className="pill neutral">Source: FastAPI</span>
      </header>

      {isLoading && <div className="panel page-panel"><p>Loading strategy data from backend…</p></div>}
      {isError && <div className="panel page-panel"><p>Backend strategy endpoint unavailable; fallback data is being used.</p></div>}

      {!isLoading && data && (
        <>
          <section className="summary-grid">
            {summary.map((item) => (
              <article key={item.label} className="summary-card">
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </article>
            ))}
          </section>

          <section className="panel strategy-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Strategy engine</p>
                <h3>Live fleet overview</h3>
              </div>
              <button className="quiet-button">Sync configs</button>
            </div>

            <div className="strategy-table-wrap">
              <table className="positions-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Mode</th>
                    <th>Status</th>
                    <th>Signal</th>
                    <th>Confidence</th>
                    <th>Warmup</th>
                    <th>Last candle</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((strategy) => (
                    <tr key={strategy.id}>
                      <td>
                        <div className="strategy-name-cell">
                          <strong>{strategy.name}</strong>
                          <small>{strategy.version}</small>
                        </div>
                      </td>
                      <td>{strategy.mode}</td>
                      <td>
                        <span className={`status-pill ${strategy.enabled ? 'online' : 'neutral'}`}>
                          {strategy.status}
                        </span>
                      </td>
                      <td>{strategy.signal}</td>
                      <td>{strategy.confidence}</td>
                      <td>{strategy.warmup}</td>
                      <td>{strategy.lastCandle}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="content-grid compact-grid">
            <div className="panel">
              <div className="panel-header compact">
                <div>
                  <p className="eyebrow">Signal details</p>
                  <h3>Decision rationale</h3>
                </div>
              </div>
              <div className="timeline">
                {decisionDetails.length > 0 ? decisionDetails.map((item) => (
                  <div className="timeline-item" key={item.name}>
                    <span className={`dot ${item.tone}`} />
                    <div>
                      <strong>{item.name}</strong>
                      <p>{item.detail}</p>
                    </div>
                    <time>{item.state}</time>
                  </div>
                )) : (
                  <div className="timeline-item">
                    <span className="dot neutral" />
                    <div>
                      <strong>No strategy signal</strong>
                      <p>Strategy data is not yet available from the backend.</p>
                    </div>
                    <time>—</time>
                  </div>
                )}
              </div>
            </div>

            <div className="panel">
              <div className="panel-header compact">
                <div>
                  <p className="eyebrow">Config</p>
                  <h3>Session model</h3>
                </div>
              </div>
              <div className="bullet-list">
                <div><span>Grid mode</span><strong>Practice</strong></div>
                <div><span>Warmup</span><strong>96 candles</strong></div>
                <div><span>Timeframe</span><strong>M5</strong></div>
                <div><span>Stop policy</span><strong>Adaptive ATR</strong></div>
                <div><span>Risk cap</span><strong>0.72% daily</strong></div>
              </div>
            </div>
          </section>
        </>
      )}
    </>
  )
}
