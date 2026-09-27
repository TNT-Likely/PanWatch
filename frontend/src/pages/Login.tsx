import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Lock, Eye, EyeOff, User, Languages } from 'lucide-react'
import { authApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'
import { changeLocale, normalizeLocale } from '@/i18n'

export default function LoginPage() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const { t, i18n: i18nInstance } = useTranslation(['auth', 'common', 'settings'])
  const currentLocale = normalizeLocale(i18nInstance.resolvedLanguage || i18nInstance.language)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [isSetup, setIsSetup] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    // 检查认证状态
    authApi.status()
      .then(data => {
        setIsSetup(!data.initialized)
        setChecking(false)
      })
      .catch(() => setChecking(false))
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return

    if (isSetup) {
      if (password !== confirmPassword) {
        toast(t('auth:messages.passwordMismatch'), 'error')
        return
      }
      if (password.length < 6) {
        toast(t('auth:messages.passwordTooShort'), 'error')
        return
      }
    }

    setLoading(true)
    try {
      const data = isSetup
        ? await authApi.setup({ username, password })
        : await authApi.login({ username, password })

      // 保存 token
      localStorage.setItem('token', data.token)
      localStorage.setItem('token_expires', data.expires_at)

      toast(isSetup ? t('auth:messages.setupSuccess') : t('auth:messages.loginSuccess'), 'success')
      navigate('/')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('auth:messages.operationFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="fixed right-4 top-4 gap-1.5 text-xs text-muted-foreground"
        aria-label={t('settings:language.switchAria')}
        onClick={() => void changeLocale(currentLocale === 'zh-CN' ? 'en-US' : 'zh-CN')}
      >
        <Languages className="h-3.5 w-3.5" />
        {t('settings:language.quickSwitch')}
      </Button>
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mb-4">
            <TrendingUp className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-foreground">{t('common:product.name')}</h1>
          <p className="text-sm text-muted-foreground mt-1">{t('common:product.englishName')}</p>
        </div>

        {/* Form */}
        <div className="card p-6">
          <div className="flex items-center gap-2 mb-6">
            <Lock className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-semibold">
              {isSetup ? t('auth:title.setup') : t('auth:title.login')}
            </h2>
          </div>

          {isSetup && (
            <p className="text-sm text-muted-foreground mb-4">
              {t('auth:setupHint')}
            </p>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label htmlFor="login-username">{t('auth:fields.username')}</Label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  id="login-username"
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  placeholder={t('auth:fields.usernamePlaceholder')}
                  className="pl-10"
                  autoFocus
                />
              </div>
            </div>

            <div>
              <Label htmlFor="login-password">{isSetup ? t('auth:fields.passwordSetup') : t('auth:fields.password')}</Label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={isSetup ? t('auth:fields.passwordSetupPlaceholder') : t('auth:fields.passwordPlaceholder')}
                  className="pl-10 pr-10"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? t('auth:actions.hidePassword') : t('auth:actions.showPassword')}
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </Button>
              </div>
            </div>

            {isSetup && (
              <div>
                <Label htmlFor="login-confirm-password">{t('auth:fields.confirmPassword')}</Label>
                <Input
                  id="login-confirm-password"
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  placeholder={t('auth:fields.confirmPasswordPlaceholder')}
                />
              </div>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? (
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : isSetup ? (
                t('auth:actions.setup')
              ) : (
                t('auth:actions.login')
              )}
            </Button>
          </form>
        </div>

        <p className="text-center text-xs text-muted-foreground mt-6">
          {t('common:product.tagline')}
        </p>
      </div>
    </div>
  )
}
