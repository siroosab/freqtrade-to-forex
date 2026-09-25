import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'

type ExecutionMode = 'Dry-run' | 'Practice' | 'Live' | 'Backtest'
type UserRole = 'viewer' | 'operator' | 'admin'
type UiEnvironment = 'dev' | 'staging' | 'practice' | 'live'
type AlertLevel = 'info' | 'warning' | 'critical'

type UiAlert = {
  id: string
  title: string
  detail: string
  level: AlertLevel
  time: string
}

type MarketFeed = {
  instruments: Array<{ pair: string; bid: number; ask: number; spread: number; change: string }>
  strategySignals: Array<{ name: string; mode: ExecutionMode; status: string; signal: string; quality: string }>
  alerts: Array<{ title: string; detail: string }>
}

type AccountFeed = {
  equity: string
  exposure: string
  netPnl: string
  marginUsed: string
  dailyRisk: string
  drawdown: string
}

type OrdersFeed = Array<{
  id: string
  symbol: string
  side: 'BUY' | 'SELL'
  volume: string
  status: 'Pending' | 'Filled' | 'Cancelled' | 'Rejected'
  createdAt: string
  risk: string
}>

type UiState = {
  executionMode: ExecutionMode
  environment: UiEnvironment
  connectionState: 'online' | 'reconnecting' | 'offline'
  userRole: UserRole
  alerts: UiAlert[]
  marketFeed: MarketFeed | null
  accountFeed: AccountFeed | null
  ordersFeed: OrdersFeed | null
  setExecutionMode: (mode: ExecutionMode) => void
  setEnvironment: (environment: UiEnvironment) => void
  setConnectionState: (state: UiState['connectionState']) => void
  setUserRole: (role: UserRole) => void
  addAlert: (alert: Omit<UiAlert, 'id' | 'time'>) => void
  setMarketFeed: (snapshot: MarketFeed) => void
  setAccountFeed: (snapshot: AccountFeed) => void
  setOrdersFeed: (snapshot: OrdersFeed) => void
}

const safeStorage = {
  getItem: (name: string) => {
    try {
      return typeof window !== 'undefined' ? window.localStorage.getItem(name) : null
    } catch {
      return null
    }
  },
  setItem: (name: string, value: string) => {
    try {
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(name, value)
      }
    } catch {
      // Storage is unavailable or blocked; fail safely without exposing secrets.
    }
  },
  removeItem: (name: string) => {
    try {
      if (typeof window !== 'undefined') {
        window.localStorage.removeItem(name)
      }
    } catch {
      // Ignore storage errors silently.
    }
  },
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      executionMode: 'Practice',
      environment: 'practice',
      connectionState: 'online',
      userRole: 'operator',
      alerts: [
        {
          id: 'system-ready',
          title: 'System ready',
          detail: 'Environment is reporting healthy status and live monitoring is active.',
          level: 'info',
          time: new Date().toISOString(),
        },
      ],
      marketFeed: null,
      accountFeed: null,
      ordersFeed: null,
      setExecutionMode: (mode) => set({ executionMode: mode }),
      setEnvironment: (environment) =>
        set((state) => {
          const nextAlert: UiAlert = {
            id: `${environment}-${Date.now()}`,
            title: `Environment switched to ${environment}`,
            detail: `The operational UI is now focused on the ${environment} environment.`,
            level: environment === 'live' ? 'critical' : environment === 'practice' ? 'warning' : 'info',
            time: new Date().toISOString(),
          }

          return {
            environment,
            alerts: [nextAlert, ...state.alerts].slice(0, 6),
          }
        }),
      setConnectionState: (state) => set({ connectionState: state }),
      setUserRole: (role) => set({ userRole: role }),
      addAlert: (alert) =>
        set((state) => ({
          alerts: [{ id: `${alert.title}-${Date.now()}`, time: new Date().toISOString(), ...alert }, ...state.alerts].slice(0, 8),
        })),
      setMarketFeed: (snapshot) => set({ marketFeed: snapshot }),
      setAccountFeed: (snapshot) => set({ accountFeed: snapshot }),
      setOrdersFeed: (snapshot) => set({ ordersFeed: snapshot }),
    }),
    {
      name: 'fx-control-ui-preferences',
      storage: createJSONStorage(() => safeStorage),
      partialize: (state) => ({
        executionMode: state.executionMode,
        environment: state.environment,
        userRole: state.userRole,
      }),
      version: 1,
    },
  ),
)
