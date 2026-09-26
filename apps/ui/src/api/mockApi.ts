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

export type RiskConfig = { pair: string; units: string; riskBudget: string; riskBudgetMode: 'percent' | 'absolute'; leverage: string; maxExposure: string; maxExposureMode: 'percent' | 'absolute'; side: string; stopLoss: string | null; stopLossMode: 'percent' | 'price'; takeProfit: string | null; takeProfitMode: 'percent' | 'price'; averageEntry: string | null; averageEntryMode: 'percent' | 'price'; maxAdds: string; source: string }

const browserOrigin = typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8090'
const browserWebSocketOrigin = typeof window !== 'undefined'
  ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
  : 'ws://127.0.0.1:8090'
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? browserOrigin).replace(/\/$/, '')
const WS_BASE_URL = (import.meta.env.VITE_WS_BASE_URL ?? browserWebSocketOrigin).replace(/\/$/, '')

export type AiFreqaiFeatureParameters = {
  labelPeriodCandles: number
  includeShiftedCandles: number
  indicatorPeriodsCandles: number[]
  weightFactor: number
  diThreshold: number
}

export type AiFreqaiConfig = {
  trainPeriodDays: number
  backtestPeriodDays: number
  featureParameters: AiFreqaiFeatureParameters
}

export type AiConfig = {
  configRevision?: string
  updatedAt?: string
  strategyName: string
  model: 'rule-based' | 'ml' | 'hybrid'
  timeframe: 'M5' | 'M15' | 'H1'
  riskBudget: string
  featureSet: string[]
  trainingMode: 'dry-run' | 'practice' | 'backtest'
  entryThreshold: string
  exitThreshold: string
  volatilityWindow: string
  atrWindow: string
  maxSpreadPct: string
  freqai?: AiFreqaiConfig
}

export type AiReview = {
  status: 'pending' | 'approved' | 'rejected'
  strategyName: string
  model: string
  riskPolicy: string
  guardrails: string[]
  notes: string
  lastUpdated: string
  pair?: string
  timeframe?: string
  approvedRevision?: { pair: string; timeframe: string; configRevision?: string; approvedAt?: string; hyperopt?: Record<string, unknown>; freqai?: Record<string, unknown> } | null
}

export type AiStatus = {
  pair?: string
  state: string
  strategy: string
  strategyVersion: string
  modelMode: string
  configRevision: string
  modelVersion?: string
  featureSchemaHash?: string
  trainingDataHash?: string | null
  featureSchema: string[]
  executionMode: string
  environment: string
  liveExecution: boolean
  lastBacktest: { result?: string; netProfit?: string; trades?: number; updatedAt?: string } | null
  evidence: Record<string, string | string[]>
  updatedAt: string
  lastOptimizationAttempt?: string | null
  optimizationState?: string
}

export type AiSignalTrace = { time?: string; signal: string; reason: string; signalStrength?: number; entryThreshold?: number; spreadPct?: number; volatility?: number; atr?: number; sessionHour?: number; features?: string[] }

export async function getAiSignals(pair = 'EUR/USD', timeframe = 'M5'): Promise<{ pair: string; timeframe: string; strategy: string; configRevision: string; signals: AiSignalTrace[] }> {
  const response = await fetch(buildApiUrl(`/api/v1/ai/signals?pair=${encodeURIComponent(pair)}&timeframe=${timeframe}&count=60`))
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* status is enough */ }
    throw new Error(`AI signals unavailable: ${detail}`)
  }
  return response.json() as Promise<{ pair: string; timeframe: string; strategy: string; configRevision: string; signals: AiSignalTrace[] }>
}

export async function getAiModelComparison(pair = 'EUR/USD', timeframe = 'M5') {
  const response = await fetch(buildApiUrl(`/api/v1/ai/model-comparison?pair=${encodeURIComponent(pair)}&timeframe=${timeframe}&count=120`))
  if (!response.ok) throw new Error('Model comparison unavailable')
  return response.json() as Promise<{ pair: string; timeframe: string; comparison: { dataHash: string; featureSchemaHash: string; dataset: { pair: string; timeframe: string; trainRows: number; validationRows: number; oosRows: number; indicatorPeriods: number[]; includeShiftedCandles: number; trainPeriodDays: number | null; backtestPeriodDays: number | null; weightFactor: number; diThreshold: number }; regressor: { model: string; modelVersion: string; oosMae: string; oosRmse: string; directionalAccuracy: string; accepted: boolean; rejectionReasons: string[]; diFilteredOosRows: number }; classifier: { model: string; modelVersion: string; accuracy: number; f1Macro: number; accepted: boolean; rejectionReasons: string[]; classes: string[]; diFilteredOosRows: number }; baseline: { model: string; oosSamples: number; nonFlatSignals: number } } }>
}

export async function validateAiConfig(config: Partial<AiConfig>, pair = 'EUR/USD') {
  const response = await fetch(buildApiUrl(`/api/v1/ai/config/validate?pair=${encodeURIComponent(pair)}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-User-Role': 'admin', 'X-CSRF-Token': 'ai-config-validate' },
    body: JSON.stringify(config),
  })
  if (!response.ok) throw new Error('AI config validation rejected')
  return response.json() as Promise<{ valid: boolean; pair: string; effectiveConfig: AiConfig }>
}

export type AiHyperoptCandidateRow = { rank: number; entryThreshold: string; maxSpreadPct: string; objective: string; trainNetPl: string; validationNetPl: string; validationDrawdown: string; validationTrades: number; coverage: number }

export type AiHyperoptReport = {
  pair: string
  timeframe: string
  status: string
  strategy: string
  dataSource: string
  dataRevision: string
  dataHash: string
  featureSchemaHash: string
  modelVersion: string
  candidatesTested: number
  pairsTested: number
  periodsTested: number
  coverage: number
  attemptsRequested: number
  hyperoptLoss?: string
  historyMode?: 'candles' | 'days'
  historyValue?: number
  trainCandles: number
  validationCandles: number
  bestParameters: Record<string, string>
  objective: string
  train: { netPl: string; drawdown: string; trades: number }
  validation: { netPl: string; drawdown: string; trades: number }
  candidates: AiHyperoptCandidateRow[]
  reportText: string
}

export type AiHyperoptStatus = {
  pair: string
  status: 'idle' | 'running' | 'completed' | 'stopped' | 'failed'
  attemptsCompleted: number
  attemptsTotal: number
  startedAt?: string
  error?: string | null
  report?: AiHyperoptReport | null
  hasLastReport?: boolean
}

export async function startAiHyperopt(payload: { pair: string; timeframe: string; steps: number; attempts: number; historyMode?: 'candles' | 'days'; historyValue?: number; resetPrevious?: boolean; hyperoptLoss?: string }): Promise<{ pair: string; status: string; attemptsTotal: number }> {
  const response = await fetch(buildApiUrl('/api/v1/ai/hyperopt/start'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-User-Role': 'operator', 'X-CSRF-Token': 'ai-hyperopt' },
    body: JSON.stringify({ resetPrevious: true, ...payload }),
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const errorPayload = (await response.json()) as { detail?: string }
      detail = errorPayload.detail ?? detail
    } catch {
      // Keep the HTTP status when the backend did not return JSON.
    }
    throw new Error(`AI hyperopt rejected: ${detail}`)
  }
  return response.json() as Promise<{ pair: string; status: string; attemptsTotal: number }>
}

export async function getAiHyperoptStatus(pair = 'EUR/USD'): Promise<AiHyperoptStatus> {
  const response = await fetch(buildApiUrl(`/api/v1/ai/hyperopt/status?pair=${encodeURIComponent(pair)}`))
  if (!response.ok) throw new Error('Hyperopt status unavailable')
  return response.json() as Promise<AiHyperoptStatus>
}

export async function stopAiHyperopt(pair = 'EUR/USD') {
  const response = await fetch(buildApiUrl('/api/v1/ai/hyperopt/stop'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-User-Role': 'operator', 'X-CSRF-Token': 'ai-hyperopt-stop' },
    body: JSON.stringify({ pair }),
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const errorPayload = (await response.json()) as { detail?: string }
      detail = errorPayload.detail ?? detail
    } catch {
      // Keep the HTTP status when the backend did not return JSON.
    }
    throw new Error(`Stop request rejected: ${detail}`)
  }
  return response.json() as Promise<{ pair: string; status: string }>
}

export async function getAiHyperoptReport(pair = 'EUR/USD') {
  const response = await fetch(buildApiUrl(`/api/v1/ai/hyperopt/report?pair=${encodeURIComponent(pair)}`))
  if (!response.ok) throw new Error('Hyperopt report unavailable')
  return response.json() as Promise<{ pair: string; available: boolean; completedAt?: string; ageDays?: number; report?: AiHyperoptReport }>
}

export type AiHyperoptScheduler = {
  enabled: boolean
  intervalDays: number
  gapMinutes: number
  pairs: string[]
  approvedPairs: string[]
  lastRunAt: string | null
  nextRunAt: string | null
  nextRuns: Record<string, string>
  lastError: string | null
  running: boolean
}

export async function getAiHyperoptScheduler(): Promise<AiHyperoptScheduler> {
  const response = await fetch(buildApiUrl('/api/v1/ai/hyperopt/scheduler'))
  if (!response.ok) throw new Error('Hyperopt scheduler unavailable')
  return response.json() as Promise<AiHyperoptScheduler>
}

export async function saveAiHyperoptScheduler(config: { enabled: boolean; intervalDays: number; gapMinutes: number; pairs: string[] }): Promise<AiHyperoptScheduler> {
  const response = await fetch(buildApiUrl('/api/v1/ai/hyperopt/scheduler'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-User-Role': 'operator', 'X-CSRF-Token': 'hyperopt-scheduler' },
    body: JSON.stringify(config),
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* status is enough */ }
    throw new Error(`Scheduler update rejected: ${detail}`)
  }
  return response.json() as Promise<AiHyperoptScheduler>
}

export async function runAiHyperoptSchedulerNow(): Promise<{ status: string; pairs: string[]; jobs?: Array<{ pair: string; status: string }> }> {
  const response = await fetch(buildApiUrl('/api/v1/ai/hyperopt/scheduler/run-now'), {
    method: 'POST',
    headers: { 'X-User-Role': 'operator', 'X-CSRF-Token': 'hyperopt-scheduler-run' },
  })
  if (!response.ok) throw new Error('Scheduled Hyperopt start rejected')
  return response.json() as Promise<{ status: string; pairs: string[]; jobs?: Array<{ pair: string; status: string }> }>
}

export async function getAiHyperoptLossFunctions(): Promise<{ default: string; options: string[] }> {
  const response = await fetch(buildApiUrl('/api/v1/ai/hyperopt/loss-functions'))
  if (!response.ok) throw new Error('Hyperopt loss functions unavailable')
  return response.json() as Promise<{ default: string; options: string[] }>
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
  orders: [
    { id: 'ORD-1042', symbol: 'EUR/USD', side: 'BUY', volume: '1200', status: 'Filled', createdAt: '2026-09-18T09:14:22Z', risk: '0.75%' },
    { id: 'ORD-1043', symbol: 'GBP/USD', side: 'SELL', volume: '900', status: 'Pending', createdAt: '2026-09-18T09:17:10Z', risk: '0.62%' },
    { id: 'ORD-1044', symbol: 'USD/JPY', side: 'BUY', volume: '800', status: 'Cancelled', createdAt: '2026-09-18T09:20:07Z', risk: '0.48%' },
  ] satisfies Order[],
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
  aiConfig: {
    strategyName: 'FX Trend Pulse',
    model: 'hybrid',
    timeframe: 'M5',
    riskBudget: '0.72%',
    featureSet: ['trend', 'spread', 'session', 'volatility'],
    trainingMode: 'dry-run',
    entryThreshold: '0.5',
    exitThreshold: '0.0',
    volatilityWindow: '5',
    atrWindow: '14',
    maxSpreadPct: '1.0',
    freqai: {
      trainPeriodDays: 30,
      backtestPeriodDays: 7,
      featureParameters: {
        labelPeriodCandles: 2,
        includeShiftedCandles: 0,
        indicatorPeriodsCandles: [5, 14],
        weightFactor: 0.0,
        diThreshold: 0.0,
      },
    },
  } satisfies AiConfig,
  aiReview: {
    status: 'pending',
    strategyName: 'FX Trend Pulse',
    model: 'hybrid',
    riskPolicy: 'Practice-safe',
    guardrails: ['dry-run only', 'no live order execution', 'manual approval required'],
    notes: 'Awaiting manual review before Practice-safe execution approval.',
    lastUpdated: new Date().toISOString(),
  } satisfies AiReview,
}

export function buildApiUrl(path: string) {
  return `${API_BASE_URL}${path}`
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

export async function getMarketSummary(): Promise<MarketSummary> {
  return safeFetchWithFallback<MarketSummary>('/api/v1/markets/summary', 'market')
}

export async function getOrders(): Promise<Order[]> {
  return safeFetchWithFallback<Order[]>('/api/v1/orders', 'orders')
}

export async function getOrdersChart(pair = 'EUR/USD', timeframe = 'M15'): Promise<ForexChartData> {
  const response = await fetch(buildApiUrl(`/api/v1/orders/chart?pair=${encodeURIComponent(pair)}&timeframe=${timeframe}&count=120`))
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
  const response = await fetch(buildApiUrl('/api/v1/account/risk/config'), { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-User-Role': 'operator', 'X-CSRF-Token': 'risk-config' }, body: JSON.stringify(config) })
  if (!response.ok) throw new Error('Risk config rejected')
  return response.json() as Promise<RiskConfig>
}

export async function getSettings(): Promise<AppSettings> {
  return safeFetchWithFallback<AppSettings>('/api/v1/settings', 'settings')
}

export type SetupStatus = {
  configured: boolean
  environment: 'practice' | 'live'
  executionMode: 'dry_run' | 'practice'
  instruments: string[]
  pairTimeframes: Record<string, string>
  accountIdConfigured: boolean
  tokenConfigured: boolean
  strategyFile?: string
  configFile?: string
}

export type SetupPayload = {
  token: string
  accountId: string
  accountTypeCode: '002' | '003' | ''
  accountConfirmed: boolean
  liveConfirmed: boolean
  environment: 'practice' | 'live'
  executionMode: 'dry_run' | 'practice'
  instruments: string[]
  pairTimeframes: Record<string, string>
  riskFraction: string
}

export type SetupAccount = {
  accountId: string
  accountTypeCode: '002' | '003'
  accountType: 'CFD' | 'Spread Betting'
  tags: string[]
  summary: {
    alias?: string
    currency?: string
    balance?: string
    NAV?: string
    marginAvailable?: string
  } | null
  summaryAccessible: boolean
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

export async function uploadSetupFile(fileKind: 'config' | 'strategy', file: File): Promise<{ uploaded: boolean; reloadRequired: boolean }> {
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

export function getSetupFileUrl(fileKind: 'config' | 'strategy'): string {
  return buildApiUrl(`/api/v1/setup/files/${fileKind}`)
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
    status: 'Running' | 'Completed' | 'Warning'
    result: string
    netProfit: string
    drawdown: string
    trades: number
    updatedAt: string
  }>>('/api/v1/backtests', 'backtests')
}

export async function getAiConfig(pair = 'EUR/USD'): Promise<AiConfig> {
  return safeFetchWithFallback<AiConfig>(`/api/v1/ai/config?pair=${encodeURIComponent(pair)}`, 'aiConfig')
}

export async function getAiReview(pair = 'EUR/USD', timeframe = 'M5'): Promise<AiReview> {
  return safeFetchWithFallback<AiReview>(`/api/v1/ai/review?pair=${encodeURIComponent(pair)}&timeframe=${encodeURIComponent(timeframe)}`, 'aiReview')
}

export async function getAiStatus(pair = 'EUR/USD'): Promise<AiStatus> {
  try {
    const response = await fetch(buildApiUrl(`/api/v1/ai/status?pair=${encodeURIComponent(pair)}`))
    if (!response.ok) throw new Error('AI status unavailable')
    return (await response.json()) as AiStatus
  } catch {
    return {
      state: 'research/backtest-ready',
      strategy: 'ForexAIStrategyBaseline',
      strategyVersion: 'baseline-v1',
      modelMode: 'hybrid',
      configRevision: 'fallback',
      featureSchema: fallbackData.aiConfig.featureSet,
      executionMode: 'dry_run',
      environment: 'practice',
      liveExecution: false,
      lastBacktest: null,
      evidence: { historicalData: 'not-run', strategyContract: 'ForexBacktester.signal', validation: 'awaiting backtest', guardrails: ['dry-run only', 'no live order execution'] },
      updatedAt: new Date().toISOString(),
    }
  }
}

export async function saveAiConfig(config: AiConfig, pair = 'EUR/USD') {
  const response = await fetch(buildApiUrl(`/api/v1/ai/config?pair=${encodeURIComponent(pair)}`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Role': 'admin',
      'X-CSRF-Token': 'ui-config-save',
    },
    body: JSON.stringify(config),
  })

  if (!response.ok) {
    throw new Error('AI config save rejected by backend')
  }

  return response.json() as Promise<AiConfig>
}

export async function saveAiReview(review: { status: 'pending' | 'approved' | 'rejected'; notes: string; pair?: string; timeframe?: string; requireOptimization?: boolean; guardrails?: string[] }) {
  const response = await fetch(buildApiUrl('/api/v1/ai/review'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Role': 'operator',
      'X-CSRF-Token': 'review-approval',
    },
    body: JSON.stringify(review),
  })

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* status is enough */ }
    throw new Error(`AI review update rejected: ${detail}`)
  }

  return response.json() as Promise<AiReview>
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
  dataSource?: string
  warning?: string
  aiParameters?: Record<string, string | string[]>
  tradeDetails?: Array<Record<string, string | number | null>>
  result?: BacktestRunResult
}

export async function getBacktestJob(jobId: string): Promise<BacktestRunResult> {
  const response = await fetch(buildApiUrl(`/api/v1/backtests/${jobId}`))
  if (!response.ok) throw new Error('Backtest job unavailable')
  return response.json() as Promise<BacktestRunResult>
}

export async function runBacktest(payload: { pair: string; timeframe: string; steps: number; historyMode?: 'candles' | 'days'; historyValue?: number }): Promise<BacktestRunResult> {
  const response = await fetch(buildApiUrl('/api/v1/backtests/run'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Role': 'operator',
      'X-CSRF-Token': 'demo-backtest',
    },
    body: JSON.stringify({ ...payload, trackProgress: true }),
  })

  if (!response.ok) {
    throw new Error('Backtest run rejected by backend')
  }

  return response.json() as Promise<BacktestRunResult>
}

export async function submitMarketOrder(
  order: {
    symbol: string
    side: 'BUY' | 'SELL'
    volume: string
    stopLoss?: string
    takeProfit?: string
  },
  userRole: 'viewer' | 'operator' | 'admin' = 'operator',
) {
  const response = await fetch(buildApiUrl('/api/v1/orders/market'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Role': userRole,
      'X-CSRF-Token': 'demo-token',
    },
    body: JSON.stringify({
      ...order,
      clientOrderId: `ui-${Date.now()}`,
      role: userRole,
      csrf_token: 'demo-token',
    }),
  })

  if (!response.ok) {
    throw new Error('Order submission rejected by backend')
  }

  return response.json() as Promise<{ status: string; symbol: string; side: string; volume: string }>
}

export { fallbackData }
