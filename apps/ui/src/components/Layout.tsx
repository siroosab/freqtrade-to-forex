import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { logoutUser } from '../api/mockApi'
import { ServerStatusPanel } from './ServerStatusPanel'
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
  { label: 'Hyperopt', to: '/hyperopt', requiresRole: 'operator' },
  { label: 'Risk', to: '/risk' },
  { label: 'Settings', to: '/settings', requiresRole: 'admin' },
]

export function Layout() {
  const navigate = useNavigate()
  const executionMode = useUiStore((state) => state.executionMode)
  const environment = useUiStore((state) => state.environment)
  const connectionState = useUiStore((state) => state.connectionState)
  const userRole = useUiStore((state) => state.userRole)
  const setUserRole = useUiStore((state) => state.setUserRole)
  const setExecutionMode = useUiStore((state) => state.setExecutionMode)
  const setEnvironment = useUiStore((state) => state.setEnvironment)
  const signOut = async () => {
    let warning: string | undefined
    try {
      await logoutUser()
    } catch {
      warning = 'The server session could not be revoked; this browser session was cleared.'
    } finally {
      setUserRole('viewer')
      navigate('/login', {
        replace: true,
        state: { logoutWarning: warning },
      })
    }
  }

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

        <ServerStatusPanel />

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
          <button className="secondary-button" onClick={() => void signOut()} type="button">
            Sign out
          </button>
        </div>
      </aside>

      <main className="main-panel">
        <Outlet />
      </main>
    </div>
  )
}
