export const auth = {
  title: { setup: '设置访问密码', login: '登录' },
  setupHint: '首次使用，请设置访问密码以保护您的数据',
  fields: {
    username: '用户名',
    usernamePlaceholder: '请输入用户名',
    password: '密码',
    passwordSetup: '设置密码',
    passwordPlaceholder: '请输入密码',
    passwordSetupPlaceholder: '至少 6 位',
    confirmPassword: '确认密码',
    confirmPasswordPlaceholder: '再次输入密码',
  },
  actions: {
    login: '登录',
    setup: '设置密码并进入',
    showPassword: '显示密码',
    hidePassword: '隐藏密码',
  },
  messages: {
    passwordMismatch: '两次密码不一致',
    passwordTooShort: '密码长度至少 6 位',
    setupSuccess: '密码设置成功',
    loginSuccess: '登录成功',
    operationFailed: '操作失败',
  },
} as const
