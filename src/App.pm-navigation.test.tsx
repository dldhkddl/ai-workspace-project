import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from './App'
import { employees, teams } from './data/organization'

vi.mock('./hooks/useOfficeData', () => ({
  useOfficeData: () => ({
    status: 'success', data: { teams, employees }, error: null, retry: vi.fn(),
  }),
}))

vi.mock('./hooks/useOperationsFeed', () => ({
  useOperationsFeed: () => ({
    activities: [], alerts: [], connectionStatus: 'live', error: null, retry: vi.fn(),
  }),
}))

vi.mock('./components/ApprovalCenter', () => ({
  ApprovalCenter: ({ onPmResponseSubmitted }: { onPmResponseSubmitted?: (taskId: string) => void }) => (
    <main>
      <h2>승인</h2>
      <button type="button" onClick={() => onPmResponseSubmitted?.('pm-1')}>PM 선택 완료</button>
    </main>
  ),
}))

it('승인 화면에서 PM 답변을 보내면 사무실과 회의 정리판으로 이동한다', async () => {
  const user = userEvent.setup()
  render(<App />)

  await user.click(screen.getByRole('button', { name: '승인' }))
  await user.click(screen.getByRole('button', { name: 'PM 선택 완료' }))

  expect(screen.getByRole('region', { name: 'AI 직원 열린 사무실' })).toBeInTheDocument()
  expect(screen.getByRole('complementary', { name: '회의 내용 정리' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '사무실' })).toHaveClass('primary-nav__item--active')
})
