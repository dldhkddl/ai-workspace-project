import { describe, expect, it } from 'vitest'
import { employees } from './data/organization'
import { formatConnectionSummary, getConnectionStatusLabel } from './status'

describe('연결 상태 표시', () => {
  it('상태 코드에 맞는 한국어 라벨을 반환한다', () => {
    expect(getConnectionStatusLabel('not_connected')).toBe('준비 중')
    expect(getConnectionStatusLabel('awaiting_first_sync')).toBe('수집 대기')
    expect(getConnectionStatusLabel('healthy')).toBe('연결됨')
    expect(getConnectionStatusLabel('error')).toBe('오류')
  })

  it('직원 상태에서 연결·준비·오류 인원을 계산한다', () => {
    const sample = employees.slice(0, 4).map((employee, index) => ({
      ...employee,
      connection: {
        ...employee.connection,
        status: index === 0 ? 'healthy' as const : index === 1 ? 'error' as const : 'not_connected' as const,
      },
    }))

    expect(formatConnectionSummary(sample)).toBe('1명 연결 · 2명 준비 중 · 1명 오류')
  })

  it('상태 계약의 모든 상태를 누락 없이 집계한다', () => {
    const statuses = [
      'not_connected',
      'setup_required',
      'awaiting_first_sync',
      'healthy',
      'delayed',
      'error',
      'stopped',
    ] as const
    const sample = statuses.map((status, index) => ({
      ...employees[index],
      connection: { ...employees[index].connection, status },
    }))

    expect(formatConnectionSummary(sample)).toBe(
      '1명 연결 · 1명 준비 중 · 1명 설정 필요 · 1명 수집 대기 · 1명 지연 · 1명 오류 · 1명 중지',
    )
  })
})
