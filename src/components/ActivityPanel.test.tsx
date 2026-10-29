import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { ActivityPanel } from './ActivityPanel'

test('실제 활동과 경고를 표시한다', () => {
  const feed = {
    activities: [{
      id: 'activity-1', employeeId: 'worker-1', taskId: 'task-1', eventType: 'task.completed',
      title: '작업을 완료했습니다', detail: {}, correlationId: 'correlation-1',
      createdAt: '2026-08-27T01:00:00Z',
    }],
    alerts: [{
      id: 'alert-1', employeeId: 'worker-1', taskId: 'task-1', severity: 'warning' as const,
      code: 'TASK_RETRY_SCHEDULED', message: '작업 실행에 실패하여 다시 시도합니다', detail: {},
      correlationId: 'correlation-1', resolvedAt: null, createdAt: '2026-08-27T01:01:00Z',
    }],
    connectionStatus: 'live' as const, error: null, retry: vi.fn(),
  }

  render(<ActivityPanel {...feed} />)

  expect(screen.getByText('작업을 완료했습니다')).toBeInTheDocument()
  expect(screen.getByText('작업 실행에 실패하여 다시 시도합니다')).toBeInTheDocument()
  expect(screen.getByText('실시간 연결됨')).toBeInTheDocument()
})


test('API 실패 시 오류와 재시도 동작을 표시한다', () => {
  const retry = vi.fn()
  const feed = {
    activities: [], alerts: [], connectionStatus: 'error' as const,
    error: '운영 기록을 불러오지 못했습니다', retry,
  }

  render(<ActivityPanel {...feed} />)

  expect(screen.getByText('운영 기록을 불러오지 못했습니다')).toBeInTheDocument()
  screen.getByRole('button', { name: '다시 시도' }).click()
  expect(retry).toHaveBeenCalledOnce()
})
