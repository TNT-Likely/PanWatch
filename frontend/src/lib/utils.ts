import { useState, useEffect } from 'react'
export { cn } from '@panwatch/base-ui'

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

// ==================== 时间格式化工具 ====================

const MARKET_TIMEZONE = 'Asia/Shanghai'

/**
 * 格式化 ISO 时间为上海时区（仅时间）
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "15:30"
 */
export function formatTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleTimeString('zh-CN', {
      timeZone: MARKET_TIMEZONE,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

/**
 * 格式化 ISO 时间为上海时区日期时间
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "01/26 15:30"
 */
export function formatDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      timeZone: MARKET_TIMEZONE,
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
 * 格式化 ISO 时间为完整上海时区日期时间
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "2024-01-26 15:30:00"
 */
export function formatFullDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      timeZone: MARKET_TIMEZONE,
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
