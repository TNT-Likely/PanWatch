import { useEffect, useState } from 'react'
import { authApi, type AuthStatus } from '@panwatch/api/auth'

/** Every mount verifies with the server; overlapping requests are deduplicated by authApi. */
export function useAuthStatus() {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<{ checking: boolean; status: AuthStatus | null; error: boolean }>({
    checking: true, status: null, error: false,
  })

  useEffect(() => {
    let active = true
    setState({ checking: true, status: null, error: false })
    authApi.status().then(status => {
      if (active) setState({ checking: false, status, error: false })
    }).catch(() => {
      if (active) setState({ checking: false, status: null, error: true })
    })
    return () => { active = false }
  }, [attempt])

  return { ...state, retry: () => setAttempt(value => value + 1) }
}
