import { fetchAPI } from './client'

export interface VersionInfo {
  version: string
}

export const appApi = {
  version: () => fetchAPI<VersionInfo>('/version'),
}
