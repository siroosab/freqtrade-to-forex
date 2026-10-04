import { useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { loginUser } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const logoutWarning = (location.state as { logoutWarning?: string } | null)?.logoutWarning
  const setUserRole = useUiStore((state) => state.setUserRole)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      const session = await loginUser(username, password)
      setUserRole(session.role)
      const destination = (location.state as { from?: string } | null)?.from ?? '/'
      navigate(destination, { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Login failed')
    } finally {
      setIsSubmitting(false)
      setPassword('')
    }
  }

  return (
    <main className="setup-shell">
      <section className="setup-card login-card" aria-labelledby="login-title">
        <p className="eyebrow">FOREX OPERATIONS</p>
        <h1 id="login-title">Sign in</h1>
        <p>Use an account configured by the API administrator.</p>
        {logoutWarning && <p className="error-message" role="alert">{logoutWarning}</p>}
        <form className="login-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="login-username">Username</label>
          <input
            autoComplete="username"
            id="login-username"
            onChange={(event) => setUsername(event.target.value)}
            required
            value={username}
          />
          <label htmlFor="login-password">Password</label>
          <input
            autoComplete="current-password"
            id="login-password"
            onChange={(event) => setPassword(event.target.value)}
            required
            type="password"
            value={password}
          />
          {error && <p className="error-message" role="alert">{error}</p>}
          <button disabled={isSubmitting} type="submit">
            {isSubmitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </section>
    </main>
  )
}
