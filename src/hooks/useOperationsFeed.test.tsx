import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { useOperationsFeed } from './useOperationsFeed'
import {
  fetchActivities, fetchAlerts, fetchEvents, fetchLatestEventId, parseEvent,
} from '../api/operations'

vi.mock('../api/operations', () => ({
  activityFromEvent: vi.fn(() => null),
  alertFromEvent: vi.fn(() => null),
  fetchActivities: vi.fn(),
  fetchAlerts: vi.fn(),
  fetchEvents: vi.fn(),
  fetchLatestEventId: vi.fn(),
  parseEvent: vi.fn(),
}))

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((message: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  closed = false

  constructor(public readonly url: string) {
    FakeWebSocket.instances.push(this)
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.onclose?.()
  }
}

beforeEach(() => {
  window.localStorage.clear()
  FakeWebSocket.instances = []
  vi.mocked(fetchActivities).mockResolvedValue([])
  vi.mocked(fetchAlerts).mockResolvedValue([])
  vi.mocked(fetchEvents).mockResolvedValue([])
  vi.mocked(fetchLatestEventId).mockResolvedValue(20)
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => vi.unstubAllGlobals())

test('저장된 커서 이후 이벤트부터 복구한다', async () => {
  window.localStorage.setItem('operations.lastEventId', '12')
  const { unmount } = renderHook(() => useOperationsFeed())

  await waitFor(() => expect(fetchEvents).toHaveBeenCalledWith(12, expect.any(AbortSignal)))
  expect(FakeWebSocket.instances[0].url).toContain('after_id=12')
  unmount()
})

test('잘못된 실시간 메시지는 연결을 닫고 재시도 상태로 전환한다', async () => {
  vi.mocked(parseEvent).mockImplementation(() => { throw new Error('invalid') })
  const { result, unmount } = renderHook(() => useOperationsFeed())

  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))
  const websocket = FakeWebSocket.instances[0]
  act(() => websocket.onopen?.())
  expect(result.current.connectionStatus).toBe('live')

  act(() => websocket.onmessage?.({ data: '{}' }))

  expect(websocket.closed).toBe(true)
  expect(result.current.connectionStatus).toBe('retrying')
  expect(result.current.error).toContain('다시 연결')
  unmount()
})
