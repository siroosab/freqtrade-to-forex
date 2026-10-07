import { useQuery } from '@tanstack/react-query'
import { getServerExecutionStatus } from '../api/mockApi'

const EXECUTION_MODE_LABELS = {
  backtest: 'Backtest',
  dry_run: 'Dry-run',
  hyperopt: 'Hyperopt',
  live: 'Live',
  practice: 'Practice',
} as const

export function ExecutionStatusPanel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['server-execution-status'],
    queryFn: getServerExecutionStatus,
    refetchInterval: 15_000,
    retry: false,
  })

  return (
    <div className="execution-status">
      <div className="execution-status-value">
        <span>Mode</span>
        <strong className={data?.executionMode === 'live' ? 'live' : ''}>
          {data ? EXECUTION_MODE_LABELS[data.executionMode] : isLoading ? 'Loading...' : 'Unavailable'}
        </strong>
      </div>
      <div className="execution-status-value">
        <span>Environment</span>
        <strong className={data?.environment === 'live' ? 'live' : ''}>
          {data?.environment.toUpperCase() ?? (isLoading ? 'Loading...' : 'Unavailable')}
        </strong>
      </div>
      <small>
        {data
          ? `Read-only · server ${data.executionModeSource}/${data.environmentSource}`
          : isError
            ? 'Server configuration unavailable'
            : 'Reading server configuration'}
      </small>
    </div>
  )
}
