import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  getAutoHyperoptSchedule,
  saveAutoHyperoptSchedule,
  type AutoHyperoptPair,
  type AutoHyperoptResult,
  type AutoHyperoptSchedule,
} from '../api/mockApi'

const WEEKDAYS = [
  { value: 0, label: 'Monday', short: 'Mon' },
  { value: 1, label: 'Tuesday', short: 'Tue' },
  { value: 2, label: 'Wednesday', short: 'Wed' },
  { value: 3, label: 'Thursday', short: 'Thu' },
  { value: 4, label: 'Friday', short: 'Fri' },
  { value: 5, label: 'Saturday', short: 'Sat' },
  { value: 6, label: 'Sunday', short: 'Sun' },
]

type ScheduleDraft = {
  enabled: boolean
  weekdays: number[]
  time: string
  pairs: string[]
}

function pairKey(item: AutoHyperoptPair): string {
  return `${item.pair}|${item.timeframe}|${item.strategyClass}`
}

function scheduleDraft(schedule: AutoHyperoptSchedule): ScheduleDraft {
  return {
    enabled: schedule.enabled,
    weekdays: schedule.weekdays,
    time: schedule.time,
    pairs: schedule.pairs.map(pairKey),
  }
}

function relativeTime(value: string | null | undefined): string {
  if (!value) return 'No run yet'
  const elapsed = Math.max(0, Date.now() - new Date(value).getTime())
  if (!Number.isFinite(elapsed)) return 'Time unavailable'
  const minutes = Math.floor(elapsed / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function formatServerDateTime(value: string | null | undefined): string {
  if (!value) return 'Unavailable'
  const match = value.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/)
  if (!match) return 'Unavailable'
  const [, date, time, seconds = '00', offset] = match
  const zone = offset === 'Z' ? 'UTC+00:00' : `UTC${offset}`
  return `${date} ${time}:${seconds} ${zone}`
}

function schedulerWindowLabel(schedule: AutoHyperoptSchedule): string {
  if (!schedule.enabled) return 'Schedule disabled'
  if (
    schedule.scheduler.lastTriggeredDate
    && schedule.scheduler.lastTriggeredDate === schedule.scheduler.serverNow?.slice(0, 10)
  ) {
    return 'Already triggered today'
  }
  return schedule.scheduler.scheduleDue ? 'Start time reached' : 'Waiting for selected day/time'
}

function resultLabel(result: AutoHyperoptResult | null | undefined): string {
  if (!result) return 'No Hyperopt history'
  if (result.status === 'running') return 'Hyperopt in progress'
  if (result.status === 'failed') return 'Hyperopt failed'
  if (result.status === 'stopped') return 'Hyperopt stopped'
  if (result.status === 'interrupted') return 'Interrupted by server restart'
  return 'Hyperopt completed'
}

export function AutoHyperoptPage() {
  const queryClient = useQueryClient()
  const scheduleQuery = useQuery({
    queryKey: ['auto-hyperopt-schedule'],
    queryFn: getAutoHyperoptSchedule,
    refetchInterval: (query) => query.state.data?.queue.status === 'running' ? 1500 : 5000,
  })
  const [draft, setDraft] = useState<ScheduleDraft | null>(null)
  const activeQueue = scheduleQuery.data?.queue.status === 'running'
    || scheduleQuery.data?.queue.status === 'queued'
  const currentDraft = draft ?? (scheduleQuery.data ? scheduleDraft(scheduleQuery.data) : null)
  const currentEnabled = currentDraft?.enabled ?? false
  const currentWeekdays = currentDraft?.weekdays ?? [0, 3]
  const currentTime = currentDraft?.time ?? '12:00'
  const currentSelectedPairs = currentDraft?.pairs ?? []

  const approvedPairs = scheduleQuery.data?.availablePairs ?? []
  const selectedPairDetails = currentSelectedPairs.flatMap((key) => {
    const item = approvedPairs.find((candidate) => pairKey(candidate) === key)
    return item ? [{ pair: item.pair, timeframe: item.timeframe, strategyClass: item.strategyClass }] : []
  })

  const saveMutation = useMutation({
    mutationFn: saveAutoHyperoptSchedule,
    onSuccess: async (savedSchedule) => {
      setDraft(scheduleDraft(savedSchedule))
      await queryClient.invalidateQueries({ queryKey: ['auto-hyperopt-schedule'] })
    },
  })

  const toggleWeekday = (day: number) => {
    setDraft((current) => {
      const base = current ?? currentDraft ?? {
        enabled: false,
        weekdays: [0, 3],
        time: '12:00',
        pairs: [],
      }
      const currentDays = base.weekdays
      return {
        ...base,
        weekdays: currentDays.includes(day)
          ? currentDays.filter((value) => value !== day)
          : [...currentDays, day].sort((left, right) => left - right),
      }
    })
  }

  const togglePair = (item: AutoHyperoptPair) => {
    const key = pairKey(item)
    setDraft((current) => {
      const base = current ?? currentDraft ?? {
        enabled: false,
        weekdays: [0, 3],
        time: '12:00',
        pairs: [],
      }
      const currentPairs = base.pairs
      return {
        ...base,
        pairs: currentPairs.includes(key)
          ? currentPairs.filter((value) => value !== key)
          : [...currentPairs, key],
      }
    })
  }

  const saveSchedule = () => {
    saveMutation.mutate({
      enabled: currentEnabled,
      weekdays: currentWeekdays,
      time: currentTime,
      pairs: selectedPairDetails,
    })
  }

  const schedule = scheduleQuery.data
  const queue = schedule?.queue
  const daysLabel = currentWeekdays.length
    ? WEEKDAYS.filter((day) => currentWeekdays.includes(day.value)).map((day) => day.short).join(', ')
    : 'No days selected'

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">AUTOMATED STRATEGY RESEARCH</p>
          <h2>Scheduled Hyperopt queue</h2>
        </div>
        <span className={`pill ${schedule?.enabled ? 'positive' : 'neutral'}`}>
          {schedule?.enabled ? 'Schedule enabled' : 'Schedule paused'}
        </span>
      </header>

      {scheduleQuery.isPending && (
        <section className="panel auto-hyperopt-empty"><p>Loading approved strategies and schedule…</p></section>
      )}
      {scheduleQuery.isError && (
        <section className="panel auto-hyperopt-empty" role="alert">
          <p>Automatic Hyperopt schedule unavailable: {scheduleQuery.error.message}</p>
        </section>
      )}

      {schedule && (
        <>
          {activeQueue && (
            <section className="auto-hyperopt-running" role="status" aria-live="polite">
              <span className="auto-hyperopt-pulse" />
              <div>
                <strong>
                  Automatic Hyperopt is running
                  {queue?.activePair ? ` · ${queue.activePair}` : ''}
                </strong>
                <p>
                  Pair {queue?.position ?? 0} of {queue?.total ?? 0} · the next pair starts only after this Hyperopt finishes.
                  Manual Hyperopt and Backtests are locked while the queue is active.
                </p>
              </div>
            </section>
          )}

          <section className="summary-grid auto-hyperopt-summary">
            <article className="summary-card">
              <span>Selected instruments</span>
              <strong>{selectedPairDetails.length}</strong>
            </article>
            <article className="summary-card">
              <span>Weekly schedule</span>
              <strong>{daysLabel}</strong>
            </article>
            <article className="summary-card">
              <span>Queue time · {schedule.scheduler.serverTimezone ?? 'server local'}</span>
              <strong>{currentTime}</strong>
            </article>
            <article className="summary-card">
              <span>Last queue</span>
              <strong>{queue?.status ?? 'idle'}</strong>
            </article>
          </section>

          <section className="panel auto-hyperopt-diagnostics" aria-live="polite">
            <div>
              <p className="eyebrow">Scheduler diagnostics</p>
              <strong>
                {schedule.scheduler.running ? 'Scheduler is running' : 'Scheduler is not running'}
              </strong>
            </div>
            <div>
              <span>Server clock</span>
              <strong>{formatServerDateTime(schedule.scheduler.serverNow)}</strong>
            </div>
            <div>
              <span>Today's schedule</span>
              <strong>{schedulerWindowLabel(schedule)}</strong>
            </div>
            <div>
              <span>Next scheduled run</span>
              <strong>{formatServerDateTime(schedule.scheduler.nextRunAt)}</strong>
            </div>
            <div>
              <span>Last scheduler check</span>
              <strong>{formatServerDateTime(schedule.scheduler.lastCheckedAt)}</strong>
            </div>
            <p className="auto-hyperopt-diagnostic-reason">
              {schedule.scheduler.lastDecision ?? 'Waiting for the scheduler status.'}
            </p>
          </section>

          <section className="panel auto-hyperopt-schedule-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">01 · Schedule</p>
                <h3>Choose when the queue starts</h3>
              </div>
              <label className="auto-hyperopt-enable">
                <input
                  type="checkbox"
                  checked={currentEnabled}
                  onChange={(event) => setDraft((current) => ({
                    ...(current ?? currentDraft ?? {
                      enabled: false,
                      weekdays: [0, 3],
                      time: '12:00',
                      pairs: [],
                    }),
                    enabled: event.target.checked,
                  }))}
                  disabled={activeQueue}
                />
                <span>{currentEnabled ? 'Enabled' : 'Paused'}</span>
              </label>
            </div>

            <div className="auto-hyperopt-settings">
              <div className="auto-hyperopt-days">
                <span className="auto-hyperopt-field-label">Run on these weekdays</span>
                <div className="weekday-picker">
                  {WEEKDAYS.map((day) => (
                    <label className="weekday-option" key={day.value}>
                      <input
                        type="checkbox"
                        checked={currentWeekdays.includes(day.value)}
                        onChange={() => toggleWeekday(day.value)}
                        disabled={activeQueue}
                      />
                      <span>{day.label}</span>
                    </label>
                  ))}
                </div>
              </div>
              <label className="auto-hyperopt-time">
                <span className="auto-hyperopt-field-label">Queue start time · server local</span>
                <input
                  type="time"
                  value={currentTime}
                  onChange={(event) => setDraft((current) => ({
                    ...(current ?? currentDraft ?? {
                      enabled: false,
                      weekdays: [0, 3],
                      time: '12:00',
                      pairs: [],
                    }),
                    time: event.target.value,
                  }))}
                  disabled={activeQueue}
                />
                <small>For example, Monday and Thursday at 12:00.</small>
              </label>
            </div>
            <p className="auto-hyperopt-note">
              Each selected pair runs in order, one at a time. The next pair waits for the previous Hyperopt to finish,
              including when it fails.
            </p>
          </section>

          <section className="panel auto-hyperopt-pairs-panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">02 · Approved strategies</p>
                <h3>Choose instruments for the queue</h3>
              </div>
              <span className="pill neutral">{schedule.availablePairs.length} approved</span>
            </div>

            {schedule.availablePairs.length === 0 ? (
              <div className="auto-hyperopt-empty">
                <strong>No approved strategies found</strong>
                <p>Approve a completed Hyperopt result first. Only the currently approved pair, timeframe, and strategy can be queued.</p>
              </div>
            ) : (
              <div className="auto-hyperopt-pair-list">
                {schedule.availablePairs.map((item) => {
                  const result = item.lastHyperopt
                  const netPl = result?.netPl
                  const numericNetPl = netPl == null ? null : Number(netPl)
                  const parsedNetPl = numericNetPl != null && Number.isFinite(numericNetPl)
                    ? numericNetPl
                    : null
                  const resultTone = parsedNetPl == null
                    ? 'neutral'
                    : parsedNetPl >= 0 ? 'positive' : 'negative'
                  return (
                    <label className="auto-hyperopt-pair-row" key={pairKey(item)}>
                      <input
                        type="checkbox"
                        checked={currentSelectedPairs.includes(pairKey(item))}
                        onChange={() => togglePair(item)}
                        disabled={activeQueue}
                      />
                      <span className="auto-hyperopt-pair-main">
                        <strong>{item.pair}</strong>
                        <small>{item.timeframe} · {item.strategyClass}</small>
                      </span>
                      <span className="auto-hyperopt-pair-settings">
                        <strong>{item.settings?.attempts ?? '—'} attempts</strong>
                        <small>{item.settings?.stopDistanceMode ?? 'static'} stop distance</small>
                      </span>
                      <span className="auto-hyperopt-last-result">
                        <strong className={`auto-hyperopt-result ${resultTone}`}>
                          {parsedNetPl == null
                            ? resultLabel(result)
                            : `${parsedNetPl > 0 ? '+' : ''}${netPl}`}
                        </strong>
                        <small>
                          {result?.status === 'failed'
                            ? `${result.error || 'Hyperopt failed'} · ${relativeTime(result.completedAt)}`
                            : `${resultLabel(result)} · ${relativeTime(result?.completedAt)}`}
                        </small>
                      </span>
                    </label>
                  )
                })}
              </div>
            )}
          </section>

          <section className="panel auto-hyperopt-footer">
            <div>
              <p className="eyebrow">03 · Queue status</p>
              <h3>
                {queue?.message
                  ?? `Next run: ${formatServerDateTime(schedule.scheduler.nextRunAt)}`}
              </h3>
              <p>
                Saved Hyperopt options (including attempts and stop-distance mode) are reused from each approved revision.
                The broker spread is refreshed at run time; if no quote is available, the approved spread is retained.
              </p>
            </div>
            <button
              className="primary-action"
              type="button"
              onClick={saveSchedule}
              disabled={activeQueue || saveMutation.isPending || (currentEnabled && (!currentWeekdays.length || !selectedPairDetails.length))}
            >
              {saveMutation.isPending ? 'Saving…' : 'Save schedule'}
            </button>
            {saveMutation.error && <p role="alert">Schedule could not be saved: {saveMutation.error.message}</p>}
            {saveMutation.isSuccess && <p className="auto-hyperopt-saved" aria-live="polite">Schedule saved successfully.</p>}
          </section>
        </>
      )}
    </>
  )
}
