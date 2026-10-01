import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { UserAvatar } from '@/components/UserAvatar'
import defaultAvatar from '@/assets/default-avatar.svg'

describe('UserAvatar', () => {
  it('shows the soft face when no custom avatar is configured', () => {
    render(<UserAvatar alt="用户头像" />)
    expect(screen.getByRole('img').getAttribute('src')).toBe(defaultAvatar)
  })

  it('preserves uploads, falls back on load failure and accepts a new upload', () => {
    const view = render(<UserAvatar alt="用户头像" src="/old-avatar.png" />)
    const image = screen.getByRole('img')
    expect(image.getAttribute('src')).toBe('/old-avatar.png')
    fireEvent.error(image)
    expect(image.getAttribute('src')).toBe(defaultAvatar)
    view.rerender(<UserAvatar alt="用户头像" src="/new-avatar.png" />)
    expect(image.getAttribute('src')).toBe('/new-avatar.png')
  })
})
