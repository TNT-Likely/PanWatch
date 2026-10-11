import type { TranslationShape } from '../../resource-types'
import { auth as zhAuth } from '../zh-CN/auth'

export const auth = {
  title: { setup: 'Secure your installation', login: 'Sign in' },
  setupHint: 'Create a password to protect your data before using PanWatch.',
  oidcHint: 'Continue with the identity provider configured for this installation.',
  passwordFallback: 'Or sign in with your local password',
  fields: {
    username: 'Username',
    usernamePlaceholder: 'Enter your username',
    password: 'Password',
    passwordSetup: 'Create password',
    passwordPlaceholder: 'Enter your password',
    passwordSetupPlaceholder: 'At least 6 characters',
    confirmPassword: 'Confirm password',
    confirmPasswordPlaceholder: 'Enter the password again',
  },
  actions: {
    oidcLogin: 'Continue with SSO',
    continuing: 'Continuing…',
    retry: 'Retry',
    login: 'Sign in',
    setup: 'Save password and continue',
    showPassword: 'Show password',
    hidePassword: 'Hide password',
  },
  messages: {
    checking: 'Checking your session',
    statusFailed: 'Could not check sign-in options. Check your connection and retry.',
    oidcFailed: 'SSO sign-in could not be completed. Try again or contact the installation owner.',
    logoutFailed: 'Could not sign out. Your session may still be active. Please retry.',
    passwordMismatch: 'The passwords do not match',
    passwordTooShort: 'The password must be at least 6 characters',
    setupSuccess: 'Password created',
    loginSuccess: 'Signed in',
    operationFailed: 'Something went wrong',
  },
} as const satisfies TranslationShape<typeof zhAuth>
