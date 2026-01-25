import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const API_BASE = '/api'

interface ApiResponse<T> {
  code: number
  data: T
  message: string
}

// 获取存储的 token
export function getToken(): string | null {
  return localStorage.getItem('token')
}

// 清除 token 并跳转登录
export function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('token_expires')
  window.location.href = '/login'
}

// 检查是否已登录
export function isAuthenticated(): boolean {
  const token = getToken()
  if (!token) return false

  const expires = localStorage.getItem('token_expires')
  if (expires && new Date(expires) < new Date()) {
    logout()
    return false
  }
  return true
}

export async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {}

  // 添加 token
  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  if (options?.body) {
    headers['Content-Type'] = 'application/json'
  }

  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    ...options,
  })

  // 401 未授权，跳转登录
  if (res.status === 401) {
    logout()
    throw new Error('登录已过期')
  }

  const body: ApiResponse<T> = await res.json().catch(() => ({ code: res.status, data: null as T, message: `HTTP ${res.status}` }))
  if (body.code !== 0) {
    throw new Error(body.message || `HTTP ${res.status}`)
  }
  return body.data
}

export interface AIModel {
  id: number
  name: string
  service_id: number
  model: string
  is_default: boolean
}

export interface AIService {
  id: number
  name: string
  base_url: string
  api_key: string
  models: AIModel[]
}

export interface NotifyChannel {
  id: number
  name: string
  type: string
  config: Record<string, string>
  enabled: boolean
  is_default: boolean
}

export interface DataSource {
  id: number
  name: string
  type: string       // news / kline / capital_flow / quote / chart
  provider: string   // xueqiu / eastmoney / tencent
  config: Record<string, unknown>
  enabled: boolean
  priority: number
  supports_batch: boolean
  test_symbols: string[]
}
