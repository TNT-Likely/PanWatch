export const zhCN = {
  common: {
    product: {
      name: '盯盘侠',
      englishName: 'PanWatch',
      tagline: 'AI 驱动的股票监控助手',
    },
    actions: {
      reload: '重新加载',
      remindLater: '稍后提醒',
      upgrade: '去升级',
    },
    route: {
      loading: '页面加载中…',
      loadFailed: '页面加载失败',
      loadFailedHint: '请重试；如果问题持续存在，可能是浏览器缓存了旧版本页面。',
    },
    links: {
      github: 'GitHub 项目',
      logs: '查看日志',
    },
    update: {
      title: '发现新版本',
      description: '当前版本 v{{current}}，可升级到 v{{latest}}。',
      recommendation: '建议升级以获取最新功能和修复。',
    },
    markets: {
      all: '全部市场',
      CN: 'A股',
      HK: '港股',
      US: '美股',
    },
  },
  auth: {
    title: {
      setup: '设置访问密码',
      login: '登录',
    },
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
  },
  navigation: {
    items: {
      home: '首页',
      portfolio: '持仓',
      opportunities: '机会',
      paperTrading: '模拟盘',
      assistant: '助手',
      alerts: '提醒',
      agents: 'Agent',
      evaluations: '验证中心',
      history: '历史',
      dataSources: '数据源',
      settings: '设置',
    },
  },
  settings: {
    account: {
      menuTitle: '账户与设置',
      avatarAlt: '头像',
      themeTitle: '主题',
      theme: {
        light: '亮色',
        dark: '暗色',
        system: '跟随系统',
      },
      selfCheck: '系统自检',
      signOut: '退出登录',
    },
    language: {
      title: '界面语言',
      simplifiedChinese: '简体中文',
      englishExperimental: 'English（实验性）',
      switchAria: '切换界面语言',
      quickSwitch: 'English',
    },
  },
} as const

type TranslationShape<T> = {
  [Key in keyof T]: T[Key] extends string ? string : TranslationShape<T[Key]>
}

export const enUS = {
  common: {
    product: {
      name: 'PanWatch',
      englishName: 'PanWatch',
      tagline: 'AI-powered market monitoring assistant',
    },
    actions: {
      reload: 'Reload',
      remindLater: 'Remind me later',
      upgrade: 'View update',
    },
    route: {
      loading: 'Loading page…',
      loadFailed: 'Failed to load this page',
      loadFailedHint: 'Try again. If the problem continues, your browser may have cached an older version of the page.',
    },
    links: {
      github: 'GitHub repository',
      logs: 'View logs',
    },
    update: {
      title: 'Update available',
      description: 'You are using v{{current}}. Version v{{latest}} is available.',
      recommendation: 'Upgrade to get the latest features and fixes.',
    },
    markets: {
      all: 'All markets',
      CN: 'China A-shares',
      HK: 'Hong Kong',
      US: 'United States',
    },
  },
  auth: {
    title: {
      setup: 'Secure your installation',
      login: 'Sign in',
    },
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
  },
  navigation: {
    items: {
      home: 'Home',
      portfolio: 'Portfolio',
      opportunities: 'Opportunities',
      paperTrading: 'Paper trading',
      assistant: 'Assistant',
      alerts: 'Alerts',
      agents: 'Agents',
      evaluations: 'Evaluation',
      history: 'History',
      dataSources: 'Data sources',
      settings: 'Settings',
    },
  },
  settings: {
    account: {
      menuTitle: 'Account and settings',
      avatarAlt: 'Avatar',
      themeTitle: 'Theme',
      theme: {
        light: 'Light',
        dark: 'Dark',
        system: 'System',
      },
      selfCheck: 'System check',
      signOut: 'Sign out',
    },
    language: {
      title: 'Interface language',
      simplifiedChinese: '简体中文',
      englishExperimental: 'English (Experimental)',
      switchAria: 'Switch interface language',
      quickSwitch: '简体中文',
    },
  },
} as const satisfies TranslationShape<typeof zhCN>

export const resources = {
  'zh-CN': zhCN,
  'en-US': enUS,
} as const

export type NavigationItemKey = keyof typeof zhCN.navigation.items
