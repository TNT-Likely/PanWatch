export function isEnglishInterface(): boolean {
  return typeof localStorage !== 'undefined'
    && localStorage.getItem('panwatch-locale')?.toLowerCase().startsWith('en') === true
}

export function interfaceText(zhCN: string, enUS: string): string {
  return isEnglishInterface() ? enUS : zhCN
}
