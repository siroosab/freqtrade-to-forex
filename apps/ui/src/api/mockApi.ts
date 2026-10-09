import type {
  AccountSummary,
  AlertItem,
  AppSettings,
  MarketSummary,
  Order,
  RiskSummary,
  StrategySignal,
  WatchItem,
} from '../types'
import type { ForexChartData } from '../components/ForexChart'

export type RiskConfig = { pair: string; units: string; riskBudget: string; riskBudgetMode: 'percent' | 'absolute'; leverage: string; maxExposure: string; maxExposureMode: 'percent' | 'absolute'; side: 'LONG' | 'SHORT' | 'BOTH' | 'NONE'; stopLoss: string | null; stopLossMode: 'percent' | 'price' | 'pips'; takeProfit: string | null; takeProfitMode: 'percent' | 'price' | 'pips'; averageEntry: string | null; averageEntryMode: 'percent' | 'price' | 'pips'; maxAdds: string; source: string }
export type LiquidityLevel = { price: string; units: string }
export type LiveQuote = { pair: string; bid: string; ask: string; spread: string; time: string; tradeable: boolean; environment: string; displayPrecision: number; tradeUnitsPrecision: number; minimumTradeSize: string; baseCurrency?: string | null; quoteCurrency?: string | null; pipSize?: string; marginRate?: string | null; bids?: LiquidityLevel[]; asks?: LiquidityLevel[]; unitsAvailable?: Record<string, Record<string, string>> | null; accountCurrency?: string; marginAvailable?: string; quoteToAccountRate?: string | null; conversionError?: string | null }
export type BrokerTrade = { id: string; symbol: string; side: 'BUY' | 'SELL'; units: string; entryPrice: string; currentPrice: string | null; exitPrice: string | null; stopLoss: string | null; takeProfit: string | null; pnl: string; openedAt: string | null; closedAt: string | null; status: 'open' | 'closed'; manual: boolean; source: string; clientOrderId: string | null }
export type BrokerPositions = { open: BrokerTrade[]; closed: BrokerTrade[]; accountCurrency?: string }
export type BrokerPendingOrder = { id: string; symbol: string; side: 'BUY' | 'SELL'; volume: string; price: string; status: string; createdAt: string | null; risk: string; manual: boolean }
export type ServerMetrics = {
  cpuPercent: number
  memoryPercent: number
  memoryUsed: number
  memoryTotal: number
  timestamp: string
}
export type ServerExecutionStatus = {
  environment: 'practice' | 'live'
  environmentSource: 'environment' | 'config' | 'default'
  executionMode: 'backtest' | 'hyperopt' | 'dry_run' | 'practice' | 'live'
  executionModeSource: 'environment' | 'config' | 'default'
}

const browserOrigin = typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8090'
const browserWebSocketOrigin = typeof window !== 'undefined'
  ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
  : 'ws://127.0.0.1:8090'
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? browserOrigin).replace(/\/$/, '')
const WS_BASE_URL = (import.meta.env.VITE_WS_BASE_URL ?? browserWebSocketOrigin).replace(/\/$/, '')
const AUTH_SESSION_STORAGE_KEY = 'fx-control-api-session'

export type AuthSession = {
  username: string
  role: 'viewer' | 'operator' | 'admin'
  sessionToken: string
  csrfToken: string
}

export function getStoredAuthSession(): AuthSession | null {
  const serialized = window.sessionStorage.getItem(AUTH_SESSION_STORAGE_KEY)
  if (!serialized) return null
  try {
    const session = JSON.parse(serialized) as AuthSession
    if (
      typeof session.sessionToken === 'string'
      && typeof session.csrfToken === 'string'
      && typeof session.username === 'string'
      && ['viewer', 'operator', 'admin'].includes(session.role)
    ) {
      return session
    }
  } catch {
    window.sessionStorage.removeItem(AUTH_SESSION_STORAGE_KEY)
  }
  return null
}

function saveAuthSession(session: AuthSession | null): void {
  if (session) {
    window.sessionStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(session))
  } else {
    window.sessionStorage.removeItem(AUTH_SESSION_STORAGE_KEY)
  }
}

async function fetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const session = getStoredAuthSession()
  if (session) {
    headers.set('X-Session-Token', session.sessionToken)
    headers.set('X-User-Role', session.role)
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes((init.method ?? 'GET').toUpperCase())) {
      headers.set('X-CSRF-Token', session.csrfToken)
    }
  }
  const response = await globalThis.fetch(input, { ...init, headers })
  if (response.status === 401 && session) {
    saveAuthSession(null)
    window.dispatchEvent(new Event('fx-auth-expired'))
  }
  return response
}

export async function loginUser(username: string, password: string): Promise<AuthSession> {
  const response = await globalThis.fetch(buildApiUrl('/api/v1/auth/login'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail
    } catch {
      // The status still explains why authentication failed.
    }
    throw new Error(`Login failed: ${detail}`)
  }
  const payload = await response.json() as {
    user: { username: string; role: AuthSession['role'] }
    sessionToken: string
    csrfToken: string
  }
  const session: AuthSession = {
    username: payload.user.username,
    role: payload.user.role,
    sessionToken: payload.sessionToken,
    csrfToken: payload.csrfToken,
  }
  saveAuthSession(session)
  return session
}

export async function getAuthSession(): Promise<AuthSession> {
  const stored = getStoredAuthSession()
  if (!stored) throw new Error('No authenticated session')
  const response = await fetch(buildApiUrl('/api/v1/auth/session'))
  if (!response.ok) throw new Error('Session is invalid or has expired')
  const payload = await response.json() as { username: string; role: AuthSession['role'] }
  return { ...stored, username: payload.username, role: payload.role }
}

export async function logoutUser(): Promise<void> {
  try {
    const response = await fetch(buildApiUrl('/api/v1/auth/logout'), { method: 'POST' })
    if (!response.ok) throw new Error('Logout failed')
  } finally {
    saveAuthSession(null)
  }
}

export type StrategyReview = {
  status: 'pending' | 'approved' | 'rejected'
  strategyClass: string
  pair: string
  timeframe: string
  notes: string
  lastUpdated: string
  approvedRevision?: {
    pair: string
    timeframe: string
    strategyClass: string
    approvedAt: string
    hyperopt?: Record<string, unknown> & {
      trailingStopLoss?: boolean
      stopLoss?: {
        mode: 'pips' | 'percent' | 'money'
        value: string
        unit: string
        optimized: boolean
      }
    }
  } | null
}

export type HyperoptCandidateRow = {
  rank: number
  trailingStopLoss?: boolean
  parameters: Record<string, string | number | boolean>
  minimal_roi?: Record<string, number>
  stopLoss?: {
    mode: 'pips' | 'percent' | 'money'
    value: string
    unit?: string
    optimized: boolean
  }
  objective: string
  trainNetPl: string
  validationNetPl: string
  validationDrawdown: string
  validationTrades: number
}

export type HyperoptReport = {
  pair: string
  timeframe: string
  status: string
  strategy: string
  strategyClass?: string
  dataSource: string
  dataRevision: string
  dataHash: string
  candidatesTested: number
  attemptsRequested: number
  hyperoptLoss: string
  costSettings?: {
    spread: string
    slippage: string
    financingRatePerDayPercent: string
    commissionRatePercent: string
  }
  stopLoss?: {
    mode: 'pips' | 'percent' | 'money'
    value: string
    unit: string
    optimized: boolean
  }
  trailingStopLoss?: boolean
  positionSizing?: {
    mode: 'risk' | 'units' | 'account_amount'
    value: string | null
    unit: string
    accountCurrency: string
    riskFraction: string
    stopPips: string
    quoteToAccountRate: string
  }
  historyMode: 'candles' | 'days' | 'date_range'
  historyValue: number
  steps: number
  trainCandles: number
  validationCandles: number
  bestParameters: Record<string, string | number | boolean>
  bestMinimalRoi?: Record<string, number>
  objective: string
  train: { netPl: string; drawdown: string; trades: number }
  validation: { netPl: string; drawdown: string; trades: number }
  candidates: HyperoptCandidateRow[]
  reportText: string
}

export type HyperoptStatus = {
  pair: string
  timeframe?: string
  strategyClass?: string
  phase?: string
  status: 'idle' | 'running' | 'completed' | 'stopped' | 'failed'
  attemptsCompleted: number
  attemptsTotal: number
  startedAt?: string
  error?: string | null
  report?: HyperoptReport | null
  hasLastReport?: boolean
}

export type AutoHyperoptPair = {
  pair: string
  timeframe: string
  strategyClass: string
}

export type AutoHyperoptResult = {
  status: 'running' | 'completed' | 'failed' | 'stopped' | 'interrupted'
  completedAt?: string | null
  error?: string | null
  netPl?: string | null
}

export type AutoHyperoptSchedule = {
  enabled: boolean
  weekdays: number[]
  time: string
  timezone: 'server-local'
  pairs: AutoHyperoptPair[]
  availablePairs: Array<AutoHyperoptPair & {
    approvedAt?: string | null
    settings?: {
      attempts?: number | null
      stopDistanceMode?: string
    }
    selected: boolean
    lastHyperopt?: AutoHyperoptResult | null
  }>
  queue: {
    status: 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'
    startedAt?: string
    completedAt?: string
    scheduledAt?: string
    activePair?: string | null
    position?: number
    total?: number
    launchStatus?: 'preparing' | 'submitting' | 'started' | 'failed'
    launchRequestedAt?: string
    hyperoptStartedAt?: string
    launchFailedAt?: string
    launchError?: string | null
    message?: string
  }
  scheduler: {
    running: boolean
    serverNow?: string
    serverTimezone?: string
    nextRunAt?: string | null
    lastCheckedAt?: string | null
    lastTriggeredDate?: string | null
    scheduleDue?: boolean
    lastDecision?: string
  }
}

export type CandleCacheInventory = {
  instrument: string
  timeframe: string
  cachedRanges: number
  candles: number
  from: string | null
  to: string | null
  ranges: Array<{
    key: string
    kind: 'latest' | 'range'
    requestedStart: string | null
    requestedEnd: string | null
    candles: number
    from: string | null
    to: string | null
  }>
}

export type BacktestRunResult = {
  id: string
  status: string
  phase?: string
  pair?: string
  timeframe?: string
  steps?: number
  message: string
  historyProgress?: number
  backtestProgress?: number
  netPl?: string
  trades?: number
  startingBalance?: string
  endingBalance?: string
  winRate?: string
  maxDrawdown?: string
  strategy?: string
  backtestWindow?: string
  dataSource?: string
  warning?: string
  execution?: {
    riskFraction: string
    spread: string
    slippage: string
    financingRatePerDayPercent: string
    commissionRatePercent: string
    positionSizeMode: 'risk' | 'units' | 'account_amount'
    positionSize: string | null
    positionSizeUnit: string
    accountCurrency: string
    quoteToAccountRate: string
    stopLossMode: 'pips' | 'percent' | 'money'
    stopLossValue: string
    stopLossUnit: string
    stopLossOptimized: boolean
    trailingStopLoss: boolean
    configSource: 'approved-hyperopt' | 'default'
  }
  tradeDetails?: Array<Record<string, string | number | null | boolean>>
  summary?: Record<string, string | number | null>
  result?: BacktestRunResult
}
export type AutoExecutionResult = {
  pair: string
  timeframe: string
  signal?: 'long' | 'short' | 'flat' | string
  candleTime?: string
  status: string
  reason?: string
  orderId?: string
  transactionId?: string
  fillPrice?: string
  units?: number
  clientOrderId?: string
}

export type AutoExecutionStatus = {
  enabled: boolean
  environment: string
  lastCycleAt?: string | null
  lastError?: string | null
  results: AutoExecutionResult[]
}

export async function getAutoExecutionStatus(): Promise<AutoExecutionStatus> {
  const response = await fetch(buildApiUrl('/api/v1/strategy/auto-execution'))
  if (!response.ok) throw new Error('Automatic strategy execution status unavailable')
  return response.json() as Promise<AutoExecutionStatus>
}

export async function setAutoExecution(
  enabled: boolean,
): Promise<AutoExecutionStatus> {
  const response = await fetch(buildApiUrl('/api/v1/strategy/auto-execution'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ enabled }),
  })
  if (!response.ok) {
    let detail = 'Automatic strategy execution update rejected'
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail
    } catch {
      // Keep the explicit status-based error when the body is not JSON.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<AutoExecutionStatus>
}

async function throwApiError(response: Response, message: string): Promise<never> {
  let detail = `HTTP ${response.status}`
  try {
    detail = ((await response.json()) as { detail?: string }).detail ?? detail
  } catch {
    // Preserve the HTTP status when the API did not return JSON.
  }
  throw new Error(`${message}: ${detail}`)
}

export async function startHyperopt(payload: { pair: string; timeframe: string; strategyClass: string; steps: number; attempts: number; historyMode?: 'candles' | 'days' | 'date_range'; historyValue?: number; startDate?: string; endDate?: string; hyperoptLoss?: string; spread?: string; slippage?: string; financingRatePerDayPercent?: string; commissionRatePercent?: string; positionSizeMode?: 'risk' | 'units' | 'account_amount'; positionSize?: string; stopDistanceMode?: 'static' | 'automatic'; stopLossMode?: 'pips' | 'percent' | 'money'; stopLossValue?: string; trailingStopLoss?: boolean }): Promise<{ pair: string; status: string; attemptsTotal: number }> {
  const response = await fetch(buildApiUrl('/api/v1/hyperopt/start'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resetPrevious: true, ...payload }),
  })
  if (!response.ok) return throwApiError(response, 'Hyperopt request rejected')
  return response.json() as Promise<{ pair: string; status: string; attemptsTotal: number }>
}

export async function getHyperoptStatus(pair: string, strategyClass: string, timeframe: string): Promise<HyperoptStatus> {
  const params = new URLSearchParams({ pair, strategy_class: strategyClass, timeframe })
  const response = await fetch(buildApiUrl(`/api/v1/hyperopt/status?${params.toString()}`))
  if (!response.ok) return throwApiError(response, 'Hyperopt status unavailable')
  return response.json() as Promise<HyperoptStatus>
}

export async function stopHyperopt(pair: string, strategyClass: string, timeframe: string): Promise<{ pair: string; status: string }> {
  const response = await fetch(buildApiUrl('/api/v1/hyperopt/stop'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pair, strategyClass, timeframe }),
  })
  if (!response.ok) return throwApiError(response, 'Hyperopt stop request rejected')
  return response.json() as Promise<{ pair: string; status: string }>
}

export async function getHyperoptReport(pair: string, strategyClass: string, timeframe: string): Promise<{ pair: string; available: boolean; completedAt?: string; report?: HyperoptReport }> {
  const params = new URLSearchParams({ pair, strategy_class: strategyClass, timeframe })
  const response = await fetch(buildApiUrl(`/api/v1/hyperopt/report?${params.toString()}`))
  if (!response.ok) return throwApiError(response, 'Hyperopt report unavailable')
  return response.json() as Promise<{ pair: string; available: boolean; completedAt?: string; report?: HyperoptReport }>
}

export async function getHyperoptLossFunctions(): Promise<{ default: string; options: string[] }> {
  const response = await fetch(buildApiUrl('/api/v1/hyperopt/loss-functions'))
  if (!response.ok) return throwApiError(response, 'Hyperopt loss functions unavailable')
  return response.json() as Promise<{ default: string; options: string[] }>
}

export async function getAutoHyperoptSchedule(): Promise<AutoHyperoptSchedule> {
  const response = await fetch(buildApiUrl('/api/v1/auto-hyperopt'))
  if (!response.ok) return throwApiError(response, 'Automatic Hyperopt schedule unavailable')
  return response.json() as Promise<AutoHyperoptSchedule>
}

export async function saveAutoHyperoptSchedule(schedule: {
  enabled: boolean
  weekdays: number[]
  time: string
  pairs: AutoHyperoptPair[]
}): Promise<AutoHyperoptSchedule> {
  const response = await fetch(buildApiUrl('/api/v1/auto-hyperopt'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(schedule),
  })
  if (!response.ok) return throwApiError(response, 'Automatic Hyperopt schedule update rejected')
  return response.json() as Promise<AutoHyperoptSchedule>
}

export async function getCandleCacheInventory(
  pair: string,
  timeframe: string,
): Promise<CandleCacheInventory> {
  const params = new URLSearchParams({ pair, timeframe })
  const response = await fetch(buildApiUrl(`/api/v1/hyperopt/data-cache?${params.toString()}`))
  if (!response.ok) return throwApiError(response, 'Cached candle inventory unavailable')
  return response.json() as Promise<CandleCacheInventory>
}

export async function clearCandleCache(
  pair: string,
  timeframe: string,
): Promise<{ pair: string; timeframe: string; removedRanges: number }> {
  const params = new URLSearchParams({ pair, timeframe })
  const response = await fetch(buildApiUrl(`/api/v1/hyperopt/data-cache?${params.toString()}`), {
    method: 'DELETE',
  })
  if (!response.ok) return throwApiError(response, 'Candle cache clear rejected')
  return response.json() as Promise<{ pair: string; timeframe: string; removedRanges: number }>
}

export async function downloadCandleDateRange(payload: {
  pair: string
  timeframe: string
  startDate: string
  endDate: string
}): Promise<{
  pair: string
  timeframe: string
  startDate: string
  endDate: string
  effectiveEnd: string
  candles: number
  from: string | null
  to: string | null
  cache: CandleCacheInventory
}> {
  const response = await fetch(buildApiUrl('/api/v1/hyperopt/data-download'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) return throwApiError(response, 'Historical candle download rejected')
  return response.json() as Promise<{
    pair: string
    timeframe: string
    startDate: string
    endDate: string
    effectiveEnd: string
    candles: number
    from: string | null
    to: string | null
    cache: CandleCacheInventory
  }>
}
const fallbackData = {
  account: {
    equity: '$184,260.48',
    exposure: '$32,420.00',
    netPnl: '$8,972.18',
    marginUsed: '31.4%',
    dailyRisk: '0.72%',
    drawdown: '4.10%',
  } satisfies AccountSummary,
  market: {
    instruments: [
      { pair: 'EUR/USD', bid: 1.0906, ask: 1.0908, spread: 0.0002, change: '+0.42%' },
      { pair: 'GBP/USD', bid: 1.2794, ask: 1.2797, spread: 0.0003, change: '+0.18%' },
      { pair: 'USD/JPY', bid: 148.68, ask: 148.72, spread: 0.04, change: '-0.27%' },
      { pair: 'AUD/USD', bid: 0.6648, ask: 0.6651, spread: 0.0003, change: '+0.32%' },
    ] satisfies WatchItem[],
    strategySignals: [
      { name: 'FX Trend Pulse', mode: 'Practice', status: 'Running', signal: 'Buy bias', quality: '84%' },
      { name: 'Breakout Guard', mode: 'Dry-run', status: 'Watching', signal: 'Neutral', quality: '76%' },
      { name: 'Carry Edge', mode: 'Backtest', status: 'Validated', signal: 'Short bias', quality: '91%' },
    ] satisfies StrategySignal[],
    alerts: [
      { title: 'Risk check passed', detail: 'Daily loss remains within policy threshold.' },
      { title: 'Session rollover', detail: 'London close overlap is active for EUR/USD.' },
      { title: 'Order validation', detail: 'Client order ID confirmed and idempotency check passed.' },
    ] satisfies AlertItem[],
  } satisfies MarketSummary,
  risk: {
    dailyLoss: '$1,420.20',
    maxExposure: '$45,000.00',
    marginLevel: '130.4%',
    killSwitch: false,
    exposureByPair: [
      { pair: 'EUR/USD', value: '$15.4k' },
      { pair: 'GBP/USD', value: '$12.9k' },
      { pair: 'USD/JPY', value: '$8.1k' },
    ],
  } satisfies RiskSummary,
  settings: {
    environment: 'Practice',
    broker: 'OANDA',
    database: 'SQLite',
    websocket: 'Connected',
  } satisfies AppSettings,
  strategies: [
    {
      id: 'fx-trend-pulse',
      name: 'FX Trend Pulse',
      mode: 'Practice',
      status: 'Running',
      signal: 'Buy bias',
      confidence: '84%',
      version: 'v2.8.1',
      warmup: '96 candles',
      lastCandle: 'M5 • 09:35',
      enabled: true,
    },
    {
      id: 'breakout-guard',
      name: 'Breakout Guard',
      mode: 'Dry-run',
      status: 'Watching',
      signal: 'Neutral',
      confidence: '76%',
      version: 'v1.4.9',
      warmup: '72 candles',
      lastCandle: 'M15 • 09:20',
      enabled: false,
    },
    {
      id: 'carry-edge',
      name: 'Carry Edge',
      mode: 'Backtest',
      status: 'Validated',
      signal: 'Short bias',
      confidence: '91%',
      version: 'v3.0.2',
      warmup: '120 candles',
      lastCandle: 'H1 • 08:00',
      enabled: true,
    },
  ],
  backtests: [
    {
      id: 'bt-2026-09-18-01',
      name: 'EUR/USD Multi-Session',
      pair: 'EUR/USD',
      timeframe: 'M15',
      status: 'Completed',
      result: '+4.61%',
      netProfit: '+$8,420.10',
      drawdown: '5.20%',
      trades: 62,
      updatedAt: '2026-09-18T09:42:00Z',
    },
    {
      id: 'bt-2026-09-18-02',
      name: 'GBP/JPY Volatility',
      pair: 'GBP/JPY',
      timeframe: 'H1',
      status: 'Running',
      result: 'Processing',
      netProfit: '+$2,960.40',
      drawdown: '3.10%',
      trades: 28,
      updatedAt: '2026-09-18T09:26:00Z',
    },
    {
      id: 'bt-2026-09-18-03',
      name: 'USD/CHF Carry Filter',
      pair: 'USD/CHF',
      timeframe: 'H4',
      status: 'Warning',
      result: '+1.08%',
      netProfit: '+$1,640.90',
      drawdown: '8.40%',
      trades: 17,
      updatedAt: '2026-09-18T08:42:00Z',
    },
  ],
}

export function buildApiUrl(path: string) {
  return `${API_BASE_URL}${path}`
}

export function createManualClientOrderId() {
  const cryptoApi = globalThis.crypto
  if (typeof cryptoApi?.randomUUID === 'function') {
    return `manual-ui-${cryptoApi.randomUUID()}`
  }

  if (typeof cryptoApi?.getRandomValues === 'function') {
    const values = cryptoApi.getRandomValues(new Uint32Array(4))
    const suffix = Array.from(values, (value) => value.toString(16).padStart(8, '0')).join('')
    return `manual-ui-${Date.now()}-${suffix}`
  }

  return `manual-ui-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function buildSocketUrl(path: string) {
  const base = WS_BASE_URL.replace(/^ws:/, 'ws:').replace(/^wss:/, 'wss:')
  return `${base}${path}`
}

async function safeFetchWithFallback<T>(path: string, fallbackKey: keyof typeof fallbackData): Promise<T> {
  try {
    const response = await fetch(buildApiUrl(path))
    if (!response.ok) {
      throw new Error('Backend unavailable')
    }
    return (await response.json()) as T
  } catch {
    return fallbackData[fallbackKey] as T
  }
}

export async function getAccountSummary(): Promise<AccountSummary> {
  return safeFetchWithFallback<AccountSummary>('/api/v1/account/summary', 'account')
}

export async function getServerMetrics(): Promise<ServerMetrics> {
  const response = await fetch(buildApiUrl('/api/v1/system/metrics'))
  if (!response.ok) throw new Error('Server metrics unavailable')
  return response.json() as Promise<ServerMetrics>
}

export async function getServerExecutionStatus(): Promise<ServerExecutionStatus> {
  const response = await fetch(buildApiUrl('/api/v1/system/execution-status'))
  if (!response.ok) throw new Error('Server execution status unavailable')
  return response.json() as Promise<ServerExecutionStatus>
}

export async function getMarketSummary(): Promise<MarketSummary> {
  return safeFetchWithFallback<MarketSummary>('/api/v1/markets/summary', 'market')
}

export async function getMarketQuote(pair: string): Promise<LiveQuote> {
  const response = await fetch(buildApiUrl(`/api/v1/markets/quote?pair=${encodeURIComponent(pair)}`))
  if (!response.ok) throw new Error('Live broker quote unavailable')
  return response.json() as Promise<LiveQuote>
}

export async function getBrokerPositions(): Promise<BrokerPositions> {
  const response = await fetch(buildApiUrl('/api/v1/positions'))
  if (!response.ok) throw new Error('Broker positions unavailable')
  return response.json() as Promise<BrokerPositions>
}

export async function getBrokerPendingOrders(): Promise<BrokerPendingOrder[]> {
  const response = await fetch(buildApiUrl('/api/v1/orders/pending'))
  if (!response.ok) throw new Error('Broker pending orders unavailable')
  return response.json() as Promise<BrokerPendingOrder[]>
}

export async function closeManualPosition(tradeId: string) {
  const response = await fetch(buildApiUrl(`/api/v1/positions/${encodeURIComponent(tradeId)}/close`), {
    method: 'POST',
  })
  if (!response.ok) {
    let detail = 'Position close rejected by broker'
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* keep default detail */ }
    throw new Error(detail)
  }
  return response.json() as Promise<{ status: string; tradeId: string; transactionId?: string; fillPrice?: string; environment?: string }>
}

export async function modifyManualPosition(
  tradeId: string,
  prices: { stopLoss: string; takeProfit: string; trailingStopLossDistance?: string | null },
) {
  const response = await fetch(buildApiUrl(`/api/v1/positions/${encodeURIComponent(tradeId)}/modify`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      stopLoss: prices.stopLoss || null,
      takeProfit: prices.takeProfit || null,
      trailingStopLossDistance: prices.trailingStopLossDistance || null,
    }),
  })
  if (!response.ok) {
    let detail = 'Position modification rejected by broker'
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* keep default detail */ }
    throw new Error(detail)
  }
  return response.json() as Promise<{ status: string; tradeId: string; transactionId?: string }>
}

async function brokerAction<T>(path: string, payload: Record<string, string | null>): Promise<T> {
  const response = await fetch(buildApiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    let detail = 'OANDA rejected the risk control'
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* status is enough */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export function applyBrokerRiskProtection(
  tradeId: string,
  payload: { stopLoss: string | null; takeProfit: string | null; trailingStopLossDistance: string | null },
) {
  return brokerAction<{ status: string; tradeId: string; transactionId?: string }>(
    `/api/v1/positions/${encodeURIComponent(tradeId)}/risk-protection`,
    payload,
  )
}

export function createAverageEntryOrder(
  tradeId: string,
  payload: { units: string; price: string; stopLoss: string | null; takeProfit: string | null },
) {
  return brokerAction<{ status: string; tradeId: string; orderId: string; transactionId?: string }>(
    `/api/v1/positions/${encodeURIComponent(tradeId)}/average-entry`,
    payload,
  )
}

export async function modifyPendingOrder(
  orderId: string,
  details: { price: string; units: string },
) {
  const response = await fetch(buildApiUrl(`/api/v1/orders/${encodeURIComponent(orderId)}/modify`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(details),
  })
  if (!response.ok) {
    let detail = 'Pending order modification rejected by broker'
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* keep default detail */ }
    throw new Error(detail)
  }
  return response.json() as Promise<{ status: string; orderId: string; transactionId?: string }>
}

export type OrderHistoryStatus = 'all' | 'open' | 'closed' | 'filled' | 'pending' | 'cancelled' | 'rejected'
export type OrderHistory = {
  orders: Order[]
  total: number
  limit: number
  status: OrderHistoryStatus
  asOf: string
  historyDays: number
}

export async function getOrders(status: OrderHistoryStatus, limit: number): Promise<OrderHistory> {
  const params = new URLSearchParams({ status, limit: String(limit) })
  const response = await fetch(buildApiUrl(`/api/v1/orders?${params.toString()}`))
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* status is enough */ }
    throw new Error(`Live order history unavailable: ${detail}`)
  }
  return response.json() as Promise<OrderHistory>
}

export async function getOrdersChart(pair = 'EUR/USD', timeframe = 'M15', count = 500): Promise<ForexChartData> {
  const response = await fetch(buildApiUrl(`/api/v1/orders/chart?pair=${encodeURIComponent(pair)}&timeframe=${encodeURIComponent(timeframe)}&count=${count}`))
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* status is enough */ }
    throw new Error(`Chart data unavailable: ${detail}`)
  }
  return response.json() as Promise<ForexChartData>
}

export async function getRiskSummary(): Promise<RiskSummary> {
  return safeFetchWithFallback<RiskSummary>('/api/v1/account/risk', 'risk')
}

export async function getRiskConfig(pair = 'EUR/USD'): Promise<RiskConfig> {
  const response = await fetch(buildApiUrl(`/api/v1/account/risk/config?pair=${encodeURIComponent(pair)}`))
  if (!response.ok) throw new Error('Risk config unavailable')
  return response.json() as Promise<RiskConfig>
}

export async function saveRiskConfig(config: RiskConfig): Promise<RiskConfig> {
  const response = await fetch(buildApiUrl('/api/v1/account/risk/config'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) })
  if (!response.ok) throw new Error('Risk config rejected')
  return response.json() as Promise<RiskConfig>
}

export async function getSettings(): Promise<AppSettings> {
  return safeFetchWithFallback<AppSettings>('/api/v1/settings', 'settings')
}

export type SetupStatus = {
  configured: boolean
  environment: 'practice' | 'live'
  executionMode: 'dry_run' | 'practice' | 'live'
  instruments: string[]
  pairTimeframes: Record<string, string>
  pairStrategies: Record<string, string>
  riskFraction: string
  accountIdConfigured: boolean
  tokenConfigured: boolean
  strategyFile?: string
  configFile?: string
}

export type SetupPayload = {
  token: string
  accountId: string
  accountTypeCode: '002' | '003' | 'PRACTICE' | ''
  accountConfirmed: boolean
  liveConfirmed: boolean
  environment: 'practice' | 'live'
  instruments: string[]
  pairTimeframes: Record<string, string>
  pairStrategies: Record<string, string>
  riskFraction: string
}

export type SetupInstrument = {
  name: string
  displayName: string
  baseCurrency?: string | null
  quoteCurrency?: string | null
  priority: 'high' | 'medium' | 'standard'
}

export type SetupAccount = {
  accountId: string
  accountTypeCode: '002' | '003' | 'PRACTICE'
  accountType: 'CFD' | 'Spread Betting' | 'Practice / V20'
  tags: string[]
  summary: {
    alias?: string
    currency?: string
    balance?: string
    NAV?: string
    marginAvailable?: string
  } | null
  summaryAccessible: boolean
  instrumentCount?: number | null
}

export type SetupDiscovery = {
  environment: 'practice' | 'live'
  accounts: SetupAccount[]
  excludedAccountCount: number
}

export async function getSetupStatus(): Promise<SetupStatus> {
  const response = await fetch(buildApiUrl('/api/v1/setup/status'))
  if (!response.ok) throw new Error('Setup status unavailable')
  return response.json() as Promise<SetupStatus>
}

export async function getSetupInstruments(payload: {
  token: string
  accountId: string
  environment: 'practice' | 'live'
}): Promise<{ environment: 'practice' | 'live'; accountId: string; instruments: SetupInstrument[] }> {
  const params = new URLSearchParams({
    token: payload.token,
    account_id: payload.accountId,
    environment: payload.environment,
  })
  const response = await fetch(buildApiUrl(`/api/v1/setup/instruments?${params.toString()}`))
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? 'Instrument list unavailable')
  }
  return response.json() as Promise<{ environment: 'practice' | 'live'; accountId: string; instruments: SetupInstrument[] }>
}

export async function discoverSetupAccounts(payload: {
  token: string
  environment: 'practice' | 'live'
}): Promise<SetupDiscovery> {
  const response = await fetch(buildApiUrl('/api/v1/setup/discover'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? 'OANDA account discovery failed')
  }
  return response.json() as Promise<SetupDiscovery>
}

export async function saveSetup(payload: SetupPayload): Promise<SetupStatus> {
  const response = await fetch(buildApiUrl('/api/v1/setup'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? 'Setup was rejected')
  }
  return response.json() as Promise<SetupStatus>
}

export type RuntimeStatus = {
  state: 'running' | 'paused' | 'stopped'
  reloadPending: boolean
  message: string
  updatedAt?: string
}

export async function getRuntimeStatus(): Promise<RuntimeStatus> {
  const response = await fetch(buildApiUrl('/api/v1/setup/runtime'))
  if (!response.ok) throw new Error('Runtime status unavailable')
  return response.json() as Promise<RuntimeStatus>
}

export async function controlRuntime(action: 'reload' | 'resume' | 'pause' | 'stop'): Promise<RuntimeStatus> {
  const response = await fetch(buildApiUrl('/api/v1/setup/runtime'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? 'Runtime action was rejected')
  }
  return response.json() as Promise<RuntimeStatus>
}

export type SystemdServiceStatus = {
  supported: boolean
  serviceName: string
  message?: string
  loadState?: string
  activeState?: string
  subState?: string
  unitFileState?: string
  logs: string[]
}

export type SystemdServiceAction =
  | 'daemon-reload'
  | 'start'
  | 'stop'
  | 'restart'
  | 'enable'
  | 'disable'
  | 'enable-now'
  | 'disable-now'

export async function getSystemdServiceStatus(): Promise<SystemdServiceStatus> {
  const response = await fetch(buildApiUrl('/api/v1/setup/service'))
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? 'Systemd service status unavailable')
  }
  return response.json() as Promise<SystemdServiceStatus>
}

export async function controlSystemdService(
  action: SystemdServiceAction,
): Promise<{ action: SystemdServiceAction; serviceName: string; queued: boolean; message: string }> {
  const response = await fetch(buildApiUrl('/api/v1/setup/service'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? 'Systemd service action was rejected')
  }
  return response.json() as Promise<{
    action: SystemdServiceAction
    serviceName: string
    queued: boolean
    message: string
  }>
}

export async function uploadSetupFile(fileKind: 'config' | 'strategy', file: File): Promise<{ uploaded: boolean; reloadRequired: boolean; strategyNames?: string[] }> {
  const content = await file.text()
  const response = await fetch(buildApiUrl(`/api/v1/setup/files/${fileKind}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, fileName: file.name }),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? `${fileKind} upload was rejected`)
  }
  return response.json() as Promise<{ uploaded: boolean; reloadRequired: boolean }>
}

export type StrategyOption = { name: string; fileName: string; builtin: boolean }

export async function getAvailableStrategies(): Promise<StrategyOption[]> {
  const response = await fetch(buildApiUrl('/api/v1/strategies/available'))
  if (!response.ok) throw new Error('Strategy catalog unavailable')
  return response.json() as Promise<StrategyOption[]>
}

export function getSetupFileUrl(fileKind: 'config' | 'strategy', strategyName?: string): string {
  const query = strategyName ? `?strategy_name=${encodeURIComponent(strategyName)}` : ''
  return buildApiUrl(`/api/v1/setup/files/${fileKind}${query}`)
}

export async function getStrategySummary(): Promise<Array<{
  id: string
  name: string
  mode: 'Dry-run' | 'Practice' | 'Live' | 'Backtest'
  status: string
  signal: string
  confidence: string
  version: string
  warmup: string
  lastCandle: string
  enabled: boolean
}>> {
  return safeFetchWithFallback<Array<{
    id: string
    name: string
    mode: 'Dry-run' | 'Practice' | 'Live' | 'Backtest'
    status: string
    signal: string
    confidence: string
    version: string
    warmup: string
    lastCandle: string
    enabled: boolean
  }>>('/api/v1/strategies', 'strategies')
}

export async function getBacktestSummary(): Promise<Array<{
  id: string
  name: string
  pair: string
  timeframe: string
  strategy?: string
  status: 'Running' | 'Completed' | 'Warning'
  result: string
  netProfit: string
  drawdown: string
  trades: number
  updatedAt: string
}>> {
  return safeFetchWithFallback<Array<{
    id: string
    name: string
    pair: string
    timeframe: string
    strategy?: string
    status: 'Running' | 'Completed' | 'Warning'
    result: string
    netProfit: string
    drawdown: string
    trades: number
    updatedAt: string
  }>>('/api/v1/backtests', 'backtests')
}

export async function getStrategyReview(pair: string, timeframe: string, strategyClass: string): Promise<StrategyReview> {
  const params = new URLSearchParams({ pair, timeframe, strategy_class: strategyClass })
  const response = await fetch(buildApiUrl(`/api/v1/strategy/review?${params.toString()}`))
  if (!response.ok) return throwApiError(response, 'Strategy approval status unavailable')
  return response.json() as Promise<StrategyReview>
}

export async function saveStrategyReview(review: { status: 'approved' | 'rejected'; pair: string; timeframe: string; strategyClass: string; requireOptimization: true; notes: string }): Promise<StrategyReview> {
  const response = await fetch(buildApiUrl('/api/v1/strategy/review'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(review),
  })
  if (!response.ok) return throwApiError(response, 'Strategy approval update rejected')
  return response.json() as Promise<StrategyReview>
}

export async function getBacktestJob(jobId: string): Promise<BacktestRunResult> {
  const response = await fetch(buildApiUrl(`/api/v1/backtests/${jobId}`))
  if (!response.ok) return throwApiError(response, 'Backtest job unavailable')
  return response.json() as Promise<BacktestRunResult>
}

export async function runBacktest(payload: { pair: string; timeframe: string; strategyClass: string; steps: number; historyMode?: 'candles' | 'days' | 'date_range'; historyValue?: number; startDate?: string; endDate?: string; spread?: string; slippage?: string; financingRatePerDayPercent?: string; commissionRatePercent?: string; positionSizeMode?: 'risk' | 'units' | 'account_amount'; positionSize?: string | null; stopLossMode?: 'pips' | 'percent' | 'money'; stopLossValue?: string }): Promise<BacktestRunResult> {
  const response = await fetch(buildApiUrl('/api/v1/backtests/run'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, trackProgress: true }),
  })
  if (!response.ok) return throwApiError(response, 'Backtest request rejected')
  return response.json() as Promise<BacktestRunResult>
}
export async function submitMarketOrder(
  order: {
    symbol: string
    side: 'BUY' | 'SELL'
    volume: number | string
    stopLoss?: string
    takeProfit?: string
    units?: number | string
    clientOrderId?: string
    riskPercent?: number
  },
) {
  const payload = {
    ...order,
    units: order.units ?? order.volume,
    clientOrderId: order.clientOrderId ?? `ui-${Date.now()}`,
    stopLoss: order.stopLoss,
    takeProfit: order.takeProfit,
  }

  const response = await fetch(buildApiUrl('/api/v1/orders/market'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    let detail = 'Order submission rejected by backend'
    try {
      const errorPayload = await response.json() as { detail?: string }
      detail = errorPayload.detail ?? detail
    } catch {
      // keep default detail if backend returned a non-JSON error body
    }
    throw new Error(detail)
  }

  return response.json() as Promise<{ status: string; symbol: string; side: string; volume: string; orderId?: string; transactionId?: string; fillPrice?: string | null; environment?: string; executionMode?: string; reason?: string | null; cancelReason?: string | null }>
}

export async function submitLimitOrder(
  order: {
    symbol: string
    side: 'BUY' | 'SELL'
    units: number | string
    price: string
    stopLoss?: string
    takeProfit?: string
    clientOrderId?: string
  },
) {
  const response = await fetch(buildApiUrl('/api/v1/orders/limit'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...order,
      clientOrderId: order.clientOrderId ?? createManualClientOrderId(),
    }),
  })
  if (!response.ok) {
    let detail = 'Limit order submission rejected by backend'
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* keep default detail */ }
    throw new Error(detail)
  }
  return response.json() as Promise<{ status: string; symbol: string; side: string; volume: string; orderId?: string; transactionId?: string; price?: string; environment?: string }>
}

export { fallbackData }
