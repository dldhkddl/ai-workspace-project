import { afterEach, expect, test, vi } from 'vitest'
import { fetchActivities, parseEvent } from './operations'

afterEach(() => vi.unstubAllGlobals())

test('형식이 잘못된 활동 API 응답을 거부한다', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify([{ id: 'broken' }]), { status: 200 }),
  ))

  await expect(fetchActivities()).rejects.toThrow('운영 기록 응답 형식이 올바르지 않습니다')
})

test('형식이 잘못된 실시간 이벤트가 커서에 적용되기 전에 거부된다', () => {
  expect(() => parseEvent({
    id: 'not-a-number', event_type: 'activity.created', resource_type: 'activity',
    resource_id: 'activity-1', payload: {}, correlation_id: null,
    created_at: '2026-08-27T01:00:00Z',
  })).toThrow('운영 기록 응답 형식이 올바르지 않습니다')
})
