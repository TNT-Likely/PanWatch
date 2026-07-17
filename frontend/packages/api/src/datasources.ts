import { fetchAPI } from './client'

export interface ResetToSeedDeletedItem {
  id: number
  type: string
  provider: string
  name: string
}

export interface ResetToSeedSeededItem {
  name: string
  type: string
  provider: string
}

export interface ResetToSeedResult {
  deleted: ResetToSeedDeletedItem[]
  seeded_missing: ResetToSeedSeededItem[]
}

/** 数据源"恢复默认":删孤儿数据源行 + 补缺失默认 + 保留用户有效自定义/凭证。 */
export const resetDataSourcesToSeed = () =>
  fetchAPI<ResetToSeedResult>('/datasources/reset-to-seed', { method: 'POST' })
