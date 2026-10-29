import { beforeEach, expect, it, vi } from 'vitest'
import { listTasks, runEmployeeTask, TaskApiError } from './tasks'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const completedTask = {
  id: 'task-1', employee_id: 'frontend', task_type: 'employee_task', title: '제목',
  status: 'completed', result: null, error: null, will_retry: false,
}

beforeEach(() => {
  vi.restoreAllMocks()
})

it('정책 거절은 다시 시도하지 않고 오류 코드와 상태를 보존한다', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({
    error: { code: 'DEVELOPMENT_MODE_PAUSED', message: '안전 구조 개선 중이라 개발 작업 요청을 잠시 받지 않습니다.' },
  }, 403))

  const error = await runEmployeeTask('frontend', '제목', '요청', vi.fn()).catch((caught) => caught)

  expect(error).toBeInstanceOf(TaskApiError)
  expect(error.code).toBe('DEVELOPMENT_MODE_PAUSED')
  expect(error.status).toBe(403)
  expect(error.retryable).toBe(false)
  expect(error.message).toBe('안전 구조 개선 중이라 개발 작업 요청을 잠시 받지 않습니다.')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('서버 오류는 같은 중복 방지 키로 한 번 더 시도한다', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(json({ error: { code: 'INTERNAL', message: '일시적인 오류입니다.' } }, 503))
    .mockResolvedValueOnce(json(completedTask, 201))

  const task = await runEmployeeTask('frontend', '제목', '요청', vi.fn())

  expect(task.status).toBe('completed')
  expect(fetchMock).toHaveBeenCalledTimes(2)
  const [, firstInit] = fetchMock.mock.calls[0]
  const [, secondInit] = fetchMock.mock.calls[1]
  const key = (init: RequestInit | undefined) =>
    (init?.headers as Record<string, string>)['Idempotency-Key']
  expect(key(firstInit)).toBe(key(secondInit))
})

it('네트워크 오류는 다시 시도 대상이다', () => {
  expect(new TaskApiError('작업 서버에 연결할 수 없습니다.').retryable).toBe(true)
  expect(new TaskApiError('요청 형식 오류', 'VALIDATION_ERROR', 422).retryable).toBe(false)
  expect(new TaskApiError('서버 오류', null, 500).retryable).toBe(true)
})

it('목록은 필터와 페이지 조건을 질의 문자열로 보낸다', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json([]))

  await listTasks({ status: 'failed', employeeId: 'frontend', limit: 50, offset: 100 })

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/tasks?status=failed&employee_id=frontend&limit=50&offset=100')
})

it('목록 응답 형식이 다르면 오류로 바꾼다', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(json([{ id: 'task-1' }]))

  await expect(listTasks()).rejects.toThrow('작업 목록 응답 형식이 올바르지 않습니다.')
})
