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

/** 数据源"恢复默认":删孤儿 + 补缺失默认,重置内置测试股票,保留用户配置/凭证。 */
export const resetDataSourcesToSeed = () =>
  fetchAPI<ResetToSeedResult>('/datasources/reset-to-seed', { method: 'POST' })
