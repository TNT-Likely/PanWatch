import { fetchAPI } from './client'

export interface ReportItem {
  job_id: string
  job_name: string
  file: string
  size: number
  mtime: number
  mtime_iso: string
  title_preview: string
}

export interface ReportListResponse {
  items: ReportItem[]
  total: number
  jobs: { job_id: string; job_name: string }[]
}

export interface ReportContentResponse {
  job_id: string
  file: string
  content: string
}

export interface SyncResult {
  synced: number
  skipped: number
  errors: string[]
  target_dir: string
}

export interface VaultStatus {
  exists: boolean
  vault_path?: string
  reports_dir?: string
  reports_count: number
  tasks?: { task_name: string; count: number }[]
  hint?: string
}

export const reportsApi = {
  list: (params?: { job_id?: string; limit?: number }) =>
    fetchAPI<ReportListResponse>(`/reports/list?${new URLSearchParams({
      ...(params?.job_id && { job_id: params.job_id }),
      ...(params?.limit && { limit: String(params.limit) }),
    } as Record<string, string>).toString()}`),

  content: (job_id: string, file: string) =>
    fetchAPI<ReportContentResponse>(`/reports/content?job_id=${encodeURIComponent(job_id)}&file=${encodeURIComponent(file)}`),

  syncToVault: (job_id?: string) =>
    fetchAPI<SyncResult>(`/reports/sync-to-vault${job_id ? `?job_id=${encodeURIComponent(job_id)}` : ''}`, {
      method: 'POST',
    }),

  vaultStatus: () => fetchAPI<VaultStatus>(`/reports/vault-status`),
}