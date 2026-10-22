import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStatus } from '@/hooks/use-auth-status'
import AuthStatusFeedback from './AuthStatusFeedback'

export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const { checking, status, error, retry } = useAuthStatus()
  const location = useLocation()
  if (checking || error || !status) return <AuthStatusFeedback checking={checking} retry={retry} />
  if (!status.authenticated) return <Navigate to="/login" state={{ from: location }} replace />
  return <>{children}</>
}
