import { useState, useEffect } from 'react'
import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * 持久化到 localStorage 的 useState
 * @param key localStorage 键名
 * @param defaultValue 默认值
 */
export function useLocalStorage<T>(key: string, defaultValue: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key)
      if (saved !== null) {
        return JSON.parse(saved)
      }
    } catch {
      // ignore
    }
    return defaultValue
  })

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // ignore
    }
  }, [key, value])

  return [value, setValue]
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

// ==================== 时间格式化工具 ====================

/**
 * 格式化 ISO 时间为本地时间（仅时间）
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "15:30"
 */
export function formatTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

/**
 * 格式化 ISO 时间为本地日期时间
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "01/26 15:30"
 */
export function formatDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

/**
 * 格式化 ISO 时间为完整本地日期时间
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "2024-01-26 15:30:00"
 */
export function formatFullDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}
