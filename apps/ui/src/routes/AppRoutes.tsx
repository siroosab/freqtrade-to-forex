import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { getAuthSession, getSetupStatus, getStoredAuthSession } from '../api/mockApi'
import { AiPage } from '../pages/AiPage'
import { BacktestAnalyticsPage } from '../pages/BacktestAnalyticsPage'
import { DashboardPage } from '../pages/DashboardPage'
import { MarketPage } from '../pages/MarketPage'
import { OrdersPage } from '../pages/OrdersPage'
import { RiskPage } from '../pages/RiskPage'
import { SettingsPage } from '../pages/SettingsPage'
import { StrategyCenterPage } from '../pages/StrategyCenterPage'
import { SetupPage } from '../pages/SetupPage'
import { LoginPage } from '../pages/LoginPage'
import { ProtectedRoute } from './ProtectedRoute'
import { useUiStore } from '../store/useUiStore'

function SetupGate() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['setup-status'], queryFn: getSetupStatus })
  if (isLoading) {
    return <main className="setup-shell"><p className="setup-loading">Checking installation status...</p></main>
  }
  if (!isError && data && !data.configured) {
    return <Navigate to="/setup" replace />
  }
  return <Outlet />
}

function AuthGate() {
  const location = useLocation()
  const setUserRole = useUiStore((state) => state.setUserRole)
  const [expired, setExpired] = useState(false)
  const hasStoredSession = Boolean(getStoredAuthSession()) && !expired
  const { data, isLoading, isError } = useQuery({
    queryKey: ['auth-session'],
    queryFn: async () => {
      const session = await getAuthSession()
      setUserRole(session.role)
      return session
    },
    enabled: hasStoredSession,
    retry: false,
  })

  useEffect(() => {
    const expire = () => {
      setExpired(true)
      setUserRole('viewer')
    }
    window.addEventListener('fx-auth-expired', expire)
    return () => window.removeEventListener('fx-auth-expired', expire)
  }, [setUserRole])

  if (!hasStoredSession || isError) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (isLoading || !data) {
    return <main className="setup-shell"><p className="setup-loading">Validating session...</p></main>
  }
  return <Outlet />
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AuthGate />}>
        <Route path="/setup" element={<SetupPage />} />
      </Route>
      <Route element={<SetupGate />}>
        <Route element={<AuthGate />}>
          <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/market" element={<MarketPage />} />
        <Route
          path="/strategies"
          element={
            <ProtectedRoute allowedRoles={['operator', 'admin']}>
              <StrategyCenterPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/orders"
          element={
            <ProtectedRoute allowedRoles={['operator', 'admin']}>
              <OrdersPage />
            </ProtectedRoute>
          }
        />
        <Route path="/backtests" element={<BacktestAnalyticsPage />} />
        <Route path="/ai" element={<AiPage />} />
        <Route path="/risk" element={<RiskPage />} />
        <Route
          path="/settings"
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <SettingsPage />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Route>
    </Routes>
  )
}
