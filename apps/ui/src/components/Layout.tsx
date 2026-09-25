import { NavLink, Outlet } from 'react-router-dom'
import { useUiStore } from '../store/useUiStore'

type NavItem = {
  label: string
  to: string
  requiresRole?: 'operator' | 'admin'
}

const navItems: NavItem[] = [
  { label: 'Overview', to: '/' },
  { label: 'Market', to: '/market' },
  { label: 'Strategies', to: '/strategies', requiresRole: 'operator' },
  { label: 'Orders', to: '/orders', requiresRole: 'operator' },
  { label: 'Backtests', to: '/backtests' },
  { label: 'AI', to: '/ai' },
  { label: 'Risk', to: '/risk' },
  { label: 'Settings', to: '/settings', requiresRole: 'admin' },
]

export function Layout() {
  const executionMode = useUiStore((state) => state.executionMode)
  const environment = useUiStore((state) => state.environment)
  const connectionState = useUiStore((state) => state.connectionState)
  const userRole = useUiStore((state) => state.userRole)
  const setUserRole = useUiStore((state) => state.setUserRole)
  const setExecutionMode = useUiStore((state) => state.setExecutionMode)
  const setEnvironment = useUiStore((state) => state.setEnvironment)

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">FX</div>
          <div>
            <p className="eyebrow">FOREX OPERATIONS</p>
            <h1>FX Control</h1>
          </div>
        </div>

        <div className="role-switcher">
          <label htmlFor="role-select">Current role</label>
          <select id="role-select" value={userRole} onChange={(event) => setUserRole(event.target.value as typeof userRole)}>
            <option value="viewer">Viewer</option>
            <option value="operator">Operator</option>
            <option value="admin">Admin</option>
          </select>
        </div>

        <nav className="nav">
          {navItems.map((item) => {
            const allowed = item.requiresRole ? userRole === item.requiresRole || userRole === 'admin' : true
            if (!allowed) {
              return null
            }

            return (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                {item.label}
              </NavLink>
            )
          })}
        </nav>

        <div className="mini-panel">
          <p className="eyebrow">Execution Mode</p>
          <select
            className="inline-select"
            value={executionMode}
            onChange={(event) => setExecutionMode(event.target.value as typeof executionMode)}
          >
            <option value="Dry-run">Dry-run</option>
            <option value="Practice">Practice</option>
            <option value="Live">Live</option>
            <option value="Backtest">Backtest</option>
          </select>

          <p className="eyebrow environment-label">Environment</p>
          <select
            className="inline-select"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value as typeof environment)}
          >
            <option value="dev">Dev</option>
            <option value="staging">Staging</option>
            <option value="practice">Practice</option>
            <option value="live">Live</option>
          </select>

          <div className="mini-meta">
            <span>Broker</span>
            <strong>OANDA</strong>
          </div>
          <div className="mini-meta">
            <span>Storage</span>
            <strong>SQLite / Postgres</strong>
          </div>
          <div className="mini-meta">
            <span>Socket</span>
            <strong>{connectionState}</strong>
          </div>
          <div className="mini-meta">
            <span>Role</span>
            <strong>{userRole}</strong>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <Outlet />
      </main>
    </div>
  )
}
