import { useState } from 'react'
import defaultAvatar from '@/assets/default-avatar.svg'

interface Props {
  src?: string | null
  alt: string
}

/** Shared portrait for account controls, including an unavailable upload. */
export function UserAvatar({ src, alt }: Props) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  const customSrc = src && src !== failedSrc ? src : null
  return <img
    src={customSrc || defaultAvatar}
    alt={alt}
    className="block h-full w-full object-cover"
    draggable={false}
    onError={customSrc ? () => setFailedSrc(customSrc) : undefined}
  />
}
