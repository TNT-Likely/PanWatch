/** Public path injected by the server, never inferred from a deep link or headers. */
export function getBasePath(): string {
  const value = typeof document === 'undefined'
    ? ''
    : document.querySelector<HTMLMetaElement>('meta[name="panwatch-base-path"]')?.content || ''
  if (value === '' || value === '/') return ''
  const path = value.endsWith('/') ? value.slice(0, -1) : value
  if (!path.startsWith('/') || path.slice(1).split('/').some(
    segment => !/^[A-Za-z0-9._~-]+$/.test(segment) || segment === '.' || segment === '..',
  )) throw new Error('Invalid PanWatch public base path')
  return path
}

/** For browser URLs. React Router destinations remain app-local (/login, etc.). */
export function publicUrl(path: string): string {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) {
    throw new Error('Expected an app-local absolute path')
  }
  return `${getBasePath()}${path}`
}
