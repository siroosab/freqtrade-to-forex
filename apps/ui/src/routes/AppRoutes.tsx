import { useQuery } from '@tanstack/react-query'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { getSetupStatus } from '../api/mockApi'
import { AiPage } from '../pages/AiPage'
import { BacktestAnalyticsPage } from '../pages/BacktestAnalyticsPage'
import { DashboardPage } from '../pages/DashboardPage'
import { MarketPage } from '../pages/MarketPage'
import { OrdersPage } from '../pages/OrdersPage'
import { RiskPage } from '../pages/RiskPage'
import { SettingsPage } from '../pages/SettingsPage'
import { StrategyCenterPage } from '../pages/StrategyCenterPage'
import { SetupPage } from '../pages/SetupPage'
import { ProtectedRoute } from './ProtectedRoute'

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

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/setup" element={<SetupPage />} />
      <Route element={<SetupGate />}>
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
    </Routes>
  )
}
