import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ConcentrationNotice } from '../src/components/ConcentrationNotice'

afterEach(cleanup)
it('shows the original ranking score and explanation', () => {
  render(<ConcentrationNotice item={{ concentration_flag: true, concentration_note: 'CN 占已投资金额 100%', raw_score: 90, raw_rank_score: 100 }} />)
  expect(screen.getByTitle('CN 占已投资金额 100%').textContent).toContain('原分 100')
})
it('does not claim safety when diagnosis is unavailable', () => {
  render(<ConcentrationNotice item={{ concentration_note: '集中度暂不可用，当前显示原始分数' }} />)
  expect(screen.getByText('集中度暂不可用')).toBeTruthy()
})
it('leaves unaffected candidates without a warning', () => {
  const { container } = render(<ConcentrationNotice item={{ concentration_flag: false }} />)
  expect(container.textContent).toBe('')
})
