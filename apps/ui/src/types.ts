export type ExecutionMode = 'Dry-run' | 'Practice' | 'Live' | 'Backtest'

export type Metric = {
  label: string
  value: string
  delta: string
  tone: 'positive' | 'negative' | 'neutral'
}

export type WatchItem = {
  pair: string
  bid: number
  ask: number
  spread: number
  change: string
}

export type Position = {
  symbol: string
  side: 'Long' | 'Short'
  units: string
  entry: string
  stop: string
  tp: string
  pnl: string
}

export type StrategySignal = {
  name: string
  mode: ExecutionMode
  status: string
  signal: string
  quality: string
}

export type AlertItem = {
  title: string
  detail: string
}

export type AccountSummary = {
  equity: string
  exposure: string
  netPnl: string
  marginUsed: string
  dailyRisk: string
  drawdown: string
}

export type MarketSummary = {
  instruments: WatchItem[]
  strategySignals: StrategySignal[]
  alerts: AlertItem[]
}

export type Order = {
  id: string
  symbol: string
  side: 'BUY' | 'SELL'
  volume: string
  status: 'Pending' | 'Filled' | 'Cancelled' | 'Rejected'
  createdAt: string
  risk: string
}

export type RiskSummary = {
  dailyLoss: string
  maxExposure: string
  marginLevel: string
  killSwitch: boolean
  exposureByPair: Array<{ pair: string; value: string }>
}

export type AppSettings = {
  environment: 'Development' | 'Practice' | 'Live'
  broker: 'OANDA' | 'Simulation'
  database: 'SQLite' | 'PostgreSQL'
  websocket: 'Connected' | 'Reconnect'
}

export type StrategySummary = {
  id: string
  name: string
  mode: ExecutionMode
  status: string
  signal: string
  confidence: string
  version: string
  warmup: string
  lastCandle: string
  enabled: boolean
}

export type BacktestRun = {
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
}
