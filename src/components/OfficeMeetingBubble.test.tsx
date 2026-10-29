import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { OfficeMeetingBubble } from './OfficeMeetingBubble'

afterEach(() => vi.useRealTimers())

it('새 회의 발언을 보여주고 잠시 뒤 자동으로 닫는다', () => {
  vi.useFakeTimers()
  const { rerender } = render(<OfficeMeetingBubble speech={null} employeeName="정보조사 담당자" />)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()

  rerender(<OfficeMeetingBubble
    speech={{ activityId: 'a1', employeeId: 'research-factcheck', message: '시장 자료를 확인했습니다.' }}
    employeeName="정보조사 담당자"
  />)

  expect(screen.getByRole('status')).toHaveTextContent('정보조사 담당자시장 자료를 확인했습니다.')
  act(() => vi.advanceTimersByTime(29_999))
  expect(screen.getByRole('status')).toBeInTheDocument()
  act(() => vi.advanceTimersByTime(1))
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
