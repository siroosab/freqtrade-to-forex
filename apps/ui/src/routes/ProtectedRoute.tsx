import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useUiStore } from '../store/useUiStore'

type Role = 'viewer' | 'operator' | 'admin'

type ProtectedRouteProps = {
  allowedRoles: Role[]
  children: ReactNode
}

export function ProtectedRoute({ allowedRoles, children }: ProtectedRouteProps) {
  const userRole = useUiStore((state) => state.userRole)

  if (!allowedRoles.includes(userRole)) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
