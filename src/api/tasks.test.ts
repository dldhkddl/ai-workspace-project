import { beforeEach, expect, it, vi } from 'vitest'
import { createEmployeeTask, getEmployeeTask, taskTypeForEmployee, TaskApiError } from './tasks'

beforeEach(() => {
  vi.restoreAllMocks()
})

it('직원 종류에 맞는 작업 유형으로 요청한다', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    id: 'task-1', employee_id: 'frontend', task_type: 'employee_task', title: '화면 수정',
    status: 'queued', result: null, error: null, will_retry: false,
  }), { status: 201, headers: { 'Content-Type': 'application/json' } }))

  await createEmployeeTask('frontend', '화면 수정', '버튼을 고쳐 주세요')

  expect(fetchMock).toHaveBeenCalledWith('/api/v1/tasks', expect.objectContaining({
    method: 'POST',
    headers: expect.objectContaining({ 'Idempotency-Key': expect.any(String) }),
    body: JSON.stringify({
      employee_id: 'frontend', task_type: 'employee_task', title: '화면 수정',
      payload: { request: '버튼을 고쳐 주세요' },
    }),
  }))
  expect(taskTypeForEmployee('general-manager')).toBe('general_manager_review')
  expect(taskTypeForEmployee('project-manager')).toBe('project_plan')
})

it('전달받은 중복 방지 키를 그대로 사용한다', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    id: 'task-1', status: 'queued',
  }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
  await createEmployeeTask('frontend', '제목', '요청', 'same-request-key')
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/tasks', expect.objectContaining({
    headers: expect.objectContaining({ 'Idempotency-Key': 'same-request-key' }),
  }))
})

it('작업 조회 오류를 사용자가 이해할 수 있는 오류로 바꾼다', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({
    error: { code: 'TASK_FAILED', message: '작업 실행에 실패했습니다.' },
  }), { status: 500, headers: { 'Content-Type': 'application/json' } }))

  await expect(getEmployeeTask('task-1')).rejects.toEqual(expect.any(TaskApiError))
  await expect(getEmployeeTask('task-1')).rejects.toThrow('작업 실행에 실패했습니다.')
})
