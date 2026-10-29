import { ApiError, isRecord, isNullableString, isString, requestJson } from './http'

export type TaskStatus = 'queued' | 'running' | 'cancel_requested' | 'cancelled' | 'completed' | 'failed'

export interface EmployeeTask {
  id: string
  employee_id: string
  task_type: string
  title: string
  status: TaskStatus
  result: Record<string, unknown> | null
  error: { code: string; message: string } | null
  will_retry: boolean
  requires_user_response?: boolean
}

export interface TaskSummary {
  id: string
  employee_id: string
  task_type: string
  title: string
  status: TaskStatus
  attempt_count: number
  max_attempts: number
  started_at: string | null
  finished_at: string | null
  created_at: string
  requires_user_response?: boolean
}

export type ProjectWorkflowStatus = 'awaiting_user' | 'replanning' | 'continued'
  | 'not_started' | 'running' | 'discussing' | 'synthesizing' | 'completed' | 'failed'

export interface ProjectWorkflow {
  status: ProjectWorkflowStatus
  plan_task_id: string
  assignments: TaskSummary[]
  discussions?: TaskSummary[]
  synthesis: EmployeeTask | null
  continuation?: EmployeeTask | null
}

export class TaskApiError extends ApiError {}

const terminalStatuses = new Set<TaskStatus>(['completed', 'failed', 'cancelled'])
const taskStatuses = new Set<TaskStatus>([
  'queued', 'running', 'cancel_requested', 'cancelled', 'completed', 'failed',
])

export const isTerminalTaskStatus = (status: TaskStatus): boolean => terminalStatuses.has(status)

const isTaskSummary = (value: unknown): value is TaskSummary => isRecord(value)
  && isString(value.id)
  && isString(value.employee_id)
  && isString(value.task_type)
  && isString(value.title)
  && isString(value.status)
  && taskStatuses.has(value.status as TaskStatus)
  && Number.isSafeInteger(value.attempt_count)
  && Number.isSafeInteger(value.max_attempts)
  && isNullableString(value.started_at)
  && isNullableString(value.finished_at)
  && isString(value.created_at)
  && (value.requires_user_response === undefined || typeof value.requires_user_response === 'boolean')

export function taskTypeForEmployee(employeeId: string): string {
  if (employeeId === 'general-manager') return 'general_manager_review'
  if (employeeId === 'project-manager') return 'project_plan'
  return 'employee_task'
}

const taskMessages = {
  connectionFailed: '작업 서버에 연결할 수 없습니다.',
  unreadable: '작업 서버 응답을 확인할 수 없습니다.',
  requestFailed: '작업 요청을 처리하지 못했습니다.',
}

const request = (path: string, init?: RequestInit) => requestJson(
  path, init, taskMessages,
  (message, code, status) => new TaskApiError(message, code, status),
)

async function taskRequest(path: string, init?: RequestInit): Promise<EmployeeTask> {
  const payload = await request(path, init)
  const task = payload as Partial<EmployeeTask>
  if (typeof task.id !== 'string' || typeof task.status !== 'string') {
    throw new TaskApiError('작업 서버 응답 형식이 올바르지 않습니다.')
  }
  return task as EmployeeTask
}

export function createEmployeeTask(
  employeeId: string, title: string, request: string, idempotencyKey: string = crypto.randomUUID(),
): Promise<EmployeeTask> {
  return taskRequest('/api/v1/tasks', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({
      employee_id: employeeId,
      task_type: taskTypeForEmployee(employeeId),
      title,
      payload: { request },
    }),
  })
}

export function getEmployeeTask(taskId: string): Promise<EmployeeTask> {
  return taskRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}`)
}

function isProjectWorkflow(value: unknown): value is ProjectWorkflow {
  if (!isRecord(value) || !isString(value.status) || !isString(value.plan_task_id)) return false
  if (!['awaiting_user', 'replanning', 'continued', 'not_started', 'running', 'discussing', 'synthesizing', 'completed', 'failed'].includes(value.status)) return false
  return Array.isArray(value.assignments) && value.assignments.every(isTaskSummary)
    && (value.discussions === undefined || (Array.isArray(value.discussions) && value.discussions.every(isTaskSummary)))
    && (value.synthesis === null || isRecord(value.synthesis))
    && (value.continuation === undefined || value.continuation === null || isRecord(value.continuation))
}

async function workflowRequest(path: string, init?: RequestInit): Promise<ProjectWorkflow> {
  const payload = await request(path, init)
  if (!isProjectWorkflow(payload)) {
    throw new TaskApiError('PM 작업 흐름 응답 형식이 올바르지 않습니다.')
  }
  return payload
}

export function getProjectWorkflow(taskId: string): Promise<ProjectWorkflow> {
  return workflowRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}/workflow`)
}

export function dispatchProjectPlan(taskId: string): Promise<ProjectWorkflow> {
  return workflowRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}/dispatch`, { method: 'POST' })
}

export function respondToProjectPlan(
  taskId: string, response: { action: 'approve' | 'revise'; message: string | null },
): Promise<EmployeeTask> {
  return taskRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(response),
  })
}

export interface TaskListQuery {
  status?: TaskStatus | null
  employeeId?: string | null
  limit?: number
  offset?: number
}

/** 목록은 요약만 받는다. 결과·오류·요청 내용은 항목을 선택할 때 상세 API로 가져온다. */
export async function listTasks(query: TaskListQuery = {}): Promise<TaskSummary[]> {
  const params = new URLSearchParams()
  if (query.status) params.set('status', query.status)
  if (query.employeeId) params.set('employee_id', query.employeeId)
  params.set('limit', String(query.limit ?? 50))
  params.set('offset', String(query.offset ?? 0))

  const payload = await request(`/api/v1/tasks?${params.toString()}`)
  if (!Array.isArray(payload) || !payload.every(isTaskSummary)) {
    throw new TaskApiError('작업 목록 응답 형식이 올바르지 않습니다.')
  }
  return payload
}

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))

export async function runEmployeeTask(
  employeeId: string,
  title: string,
  request: string,
  onUpdate: (task: EmployeeTask) => void,
): Promise<EmployeeTask> {
  const idempotencyKey = crypto.randomUUID()
  let task: EmployeeTask
  try {
    task = await createEmployeeTask(employeeId, title, request, idempotencyKey)
  } catch (error) {
    if (!(error instanceof TaskApiError) || !error.retryable) throw error
    await wait(1000)
    task = await createEmployeeTask(employeeId, title, request, idempotencyKey)
  }
  onUpdate(task)
  const deadline = Date.now() + 65 * 60 * 1000
  let consecutiveErrors = 0
  while (Date.now() < deadline && !terminalStatuses.has(task.status)) {
    await wait(consecutiveErrors === 0 ? 1000 : Math.min(1000 * 2 ** consecutiveErrors, 10_000))
    try {
      task = await getEmployeeTask(task.id)
      consecutiveErrors = 0
      onUpdate(task)
    } catch (error) {
      if (!(error instanceof TaskApiError) || !error.retryable || consecutiveErrors >= 5) throw error
      consecutiveErrors += 1
    }
  }
  if (!terminalStatuses.has(task.status)) throw new TaskApiError('작업 확인 시간이 초과되었습니다. 다시 확인해 주세요.')
  return task
}
