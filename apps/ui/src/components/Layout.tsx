import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { getSetupStatus, logoutUser } from '../api/mockApi'
import { ExecutionStatusPanel } from './ExecutionStatusPanel'
import { ServerStatusPanel } from './ServerStatusPanel'
import { ServerTime } from './ServerTime'
import { useUiStore } from '../store/useUiStore'

type NavItem = {
  label: string
  icon: string
  to: string
  requiresRole?: 'operator' | 'admin'
}

const navItems: NavItem[] = [
  { label: 'Overview', icon: '📊', to: '/' },
  { label: 'Market', icon: '🌍', to: '/market' },
  { label: 'Strategies', icon: '🧠', to: '/strategies', requiresRole: 'operator' },
  { label: 'Orders', icon: '🧾', to: '/orders', requiresRole: 'operator' },
  { label: 'Indicators', icon: '📈', to: '/indicators', requiresRole: 'operator' },
  { label: 'Hyperopt', icon: '🔬', to: '/hyperopt', requiresRole: 'operator' },
  { label: 'Auto Hyperopt', icon: '🤖', to: '/auto-hyperopt', requiresRole: 'operator' },
  { label: 'Risk', icon: '🛡️', to: '/risk' },
  { label: 'Settings', icon: '⚙️', to: '/settings', requiresRole: 'admin' },
]

export function Layout() {
  const navigate = useNavigate()
  const [serverTimestamp, setServerTimestamp] = useState<string | null>(null)
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false)
  const connectionState = useUiStore((state) => state.connectionState)
  const userRole = useUiStore((state) => state.userRole)
  const setUserRole = useUiStore((state) => state.setUserRole)
  const setSelectedInstruments = useUiStore((state) => state.setSelectedInstruments)
  const setupQuery = useQuery({ queryKey: ['setup-status'], queryFn: getSetupStatus })

  useEffect(() => {
    if (!setupQuery.data) return
    setSelectedInstruments(
      setupQuery.data.configured
        ? setupQuery.data.instruments.map((instrument) => instrument.replace(/_/g, '/'))
        : [],
    )
  }, [setSelectedInstruments, setupQuery.data])

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
      <aside className={`sidebar ${isSidebarExpanded ? 'sidebar-expanded' : 'sidebar-collapsed'}`}>
        <div className="brand-block">
          <div className="brand-mark">FX</div>
          <div className="brand-copy">
            <p className="eyebrow">🌐 FOREX OPERATIONS</p>
            <h1>FX Control</h1>
            <ServerTime key={serverTimestamp ?? 'waiting'} timestamp={serverTimestamp} variant="brand" />
          </div>
          <button
            aria-controls="sidebar-content"
            aria-expanded={isSidebarExpanded}
            aria-label={isSidebarExpanded ? 'Collapse navigation menu' : 'Expand navigation menu'}
            className="sidebar-toggle"
            onClick={() => setIsSidebarExpanded((expanded) => !expanded)}
            type="button"
          >
            <span aria-hidden="true">{isSidebarExpanded ? '✕' : '☰'}</span>
            <span>{isSidebarExpanded ? 'Close' : 'Menu'}</span>
          </button>
        </div>

        <div className="sidebar-content" id="sidebar-content">
          <nav aria-label="Main navigation" className="nav">
            {navItems.map((item) => {
              const allowed = item.requiresRole ? userRole === item.requiresRole || userRole === 'admin' : true
              if (!allowed) {
                return null
              }

              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                  onClick={() => setIsSidebarExpanded(false)}
                >
                  <span aria-hidden="true" className="nav-item-icon">{item.icon}</span>
                  <span>{item.label}</span>
                </NavLink>
              )
            })}
          </nav>

          <ServerStatusPanel onTimestampChange={setServerTimestamp} />

          <div className="mini-panel">
            <p className="eyebrow">Execution Mode</p>
            <ExecutionStatusPanel />

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
        </div>
      </aside>

      <main className="main-panel">
        <Outlet />
      </main>
    </div>
  )
}
