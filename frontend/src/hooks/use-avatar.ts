import { useState, useEffect } from 'react'
import { fetchAPI, interfaceText } from '@panwatch/api'

const EVENT = 'panwatch:avatar-changed'

// 仅 SPA 会话内的内存缓存(避免一次会话内重复请求)。
// 真正的持久化在后端 DB(data/panwatch.db 的 ui_avatar),刷新后会重新从后端拉取。
let cache: string | null = null
let inflight: Promise<string> | null = null

function load(): Promise<string> {
  if (cache !== null) return Promise.resolve(cache)
  if (!inflight) {
    inflight = fetchAPI<{ value: string }>('/settings/avatar')
      .then(r => {
        cache = r?.value || ''
        return cache as string
      })
      .catch(() => {
        cache = ''
        return ''
      })
      .finally(() => {
        inflight = null
      })
  }
  return inflight
}

/**
 * 保存头像(传空字符串=清空):后端把图片落成 data/avatars 文件、DB 仅记文件名;
 * 本地广播即时更新。注意 cache 存的是 data URL(GET 也返回 data URL)。
 */
export async function saveAvatar(value: string): Promise<void> {
  await fetchAPI('/settings/avatar', { method: 'PUT', body: JSON.stringify({ value }) })
  cache = value
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: value }))
}

/** 当前头像(data URL 或图片地址)。来源为后端 DB;跨组件即时同步。 */
export function useAvatar(): string {
  const [avatar, setAvatar] = useState<string>(cache ?? '')
  useEffect(() => {
    let alive = true
    load().then(v => {
      if (alive) setAvatar(v)
    })
    const onChange = (e: Event) => setAvatar((e as CustomEvent<string>).detail ?? '')
    window.addEventListener(EVENT, onChange)
    return () => {
      alive = false
      window.removeEventListener(EVENT, onChange)
    }
  }, [])
  return avatar
}

/**
 * 把上传的图片文件压缩为 size×size 的方形 JPEG data URL(居中裁剪),
 * 控制体积(约 10-20KB),避免大 base64 撑爆 DB 存储。
 */
export function fileToAvatarDataUrl(file: File, size = 128): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error(interfaceText('读取文件失败', 'Failed to read the image file')))
    reader.onload = () => {
      const img = new Image()
      img.onerror = () => reject(new Error(interfaceText('图片解析失败', 'Failed to decode the image')))
      img.onload = () => {
        const canvas = document.createElement('canvas')
        canvas.width = size
        canvas.height = size
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          reject(new Error(interfaceText('canvas 不可用', 'Canvas is unavailable')))
          return
        }
        const scale = Math.max(size / img.width, size / img.height)
        const w = img.width * scale
        const h = img.height * scale
        ctx.drawImage(img, (size - w) / 2, (size - h) / 2, w, h)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = reader.result as string
    }
    reader.readAsDataURL(file)
  })
}
