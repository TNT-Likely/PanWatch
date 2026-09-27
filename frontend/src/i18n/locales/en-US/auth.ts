import type { TranslationShape } from '../../resource-types'
import { auth as zhAuth } from '../zh-CN/auth'

export const auth = {
  title: { setup: 'Secure your installation', login: 'Sign in' },
  setupHint: 'Create a password to protect your data before using PanWatch.',
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
    login: 'Sign in',
    setup: 'Save password and continue',
    showPassword: 'Show password',
    hidePassword: 'Hide password',
  },
  messages: {
    passwordMismatch: 'The passwords do not match',
    passwordTooShort: 'The password must be at least 6 characters',
    setupSuccess: 'Password created',
    loginSuccess: 'Signed in',
    operationFailed: 'Something went wrong',
  },
} as const satisfies TranslationShape<typeof zhAuth>
