import type { Activity, Alert, RealtimeEvent } from '../types'

interface ApiActivity {
  id: string
  employee_id: string | null
  task_id: string | null
  event_type: string
  title: string
  detail: Record<string, unknown>
  correlation_id: string | null
  created_at: string
}

interface ApiAlert {
  id: string
  employee_id: string | null
  task_id: string | null
  severity: 'info' | 'warning' | 'error'
  code: string
  message: string
  detail: Record<string, unknown>
  correlation_id: string | null
  resolved_at: string | null
  created_at: string
}

interface ApiRealtimeEvent {
  id: number
  event_type: string
  resource_type: string
  resource_id: string
  payload: Record<string, unknown>
  correlation_id: string | null
  created_at: string
}

export class OperationsApiError extends Error {}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isString = (value: unknown): value is string => typeof value === 'string'
const isNullableString = (value: unknown): value is string | null => value === null || isString(value)
const isApiActivity = (value: unknown): value is ApiActivity => isRecord(value)
  && isString(value.id) && isNullableString(value.employee_id) && isNullableString(value.task_id)
  && isString(value.event_type) && isString(value.title) && isRecord(value.detail)
  && isNullableString(value.correlation_id) && isString(value.created_at)
const isApiAlert = (value: unknown): value is ApiAlert => isRecord(value)
  && isString(value.id) && isNullableString(value.employee_id) && isNullableString(value.task_id)
  && (value.severity === 'info' || value.severity === 'warning' || value.severity === 'error')
  && isString(value.code) && isString(value.message) && isRecord(value.detail)
  && isNullableString(value.correlation_id) && isNullableString(value.resolved_at) && isString(value.created_at)
const isApiEvent = (value: unknown): value is ApiRealtimeEvent => isRecord(value)
  && Number.isSafeInteger(value.id) && Number(value.id) >= 0
  && isString(value.event_type) && isString(value.resource_type) && isString(value.resource_id)
  && isRecord(value.payload) && isNullableString(value.correlation_id) && isString(value.created_at)

const invalidResponse = () => new OperationsApiError('운영 기록 응답 형식이 올바르지 않습니다')

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new OperationsApiError('운영 기록 서버에 연결할 수 없습니다')
  }
  if (!response.ok) throw new OperationsApiError('운영 기록을 불러오지 못했습니다')
  try {
    return await response.json() as T
  } catch {
    throw new OperationsApiError('운영 기록 응답 형식이 올바르지 않습니다')
  }
}

export const mapActivity = (item: ApiActivity): Activity => ({
  id: item.id,
  employeeId: item.employee_id,
  taskId: item.task_id,
  eventType: item.event_type,
  title: item.title,
  detail: item.detail,
  correlationId: item.correlation_id,
  createdAt: item.created_at,
})

export const mapAlert = (item: ApiAlert): Alert => ({
  id: item.id,
  employeeId: item.employee_id,
  taskId: item.task_id,
  severity: item.severity,
  code: item.code,
  message: item.message,
  detail: item.detail,
  correlationId: item.correlation_id,
  resolvedAt: item.resolved_at,
  createdAt: item.created_at,
})

export const mapEvent = (item: ApiRealtimeEvent): RealtimeEvent => ({
  id: item.id,
  eventType: item.event_type,
  resourceType: item.resource_type,
  resourceId: item.resource_id,
  payload: item.payload,
  correlationId: item.correlation_id,
  createdAt: item.created_at,
})

export async function fetchActivities(signal?: AbortSignal): Promise<Activity[]> {
  const payload = await requestJson<unknown>('/api/v1/activities?limit=50', signal)
  if (!Array.isArray(payload) || !payload.every(isApiActivity)) throw invalidResponse()
  return payload.map(mapActivity)
}

export async function fetchAlerts(signal?: AbortSignal): Promise<Alert[]> {
  const payload = await requestJson<unknown>('/api/v1/alerts?limit=50', signal)
  if (!Array.isArray(payload) || !payload.every(isApiAlert)) throw invalidResponse()
  return payload.map(mapAlert)
}

export async function fetchEvents(afterId: number, signal?: AbortSignal): Promise<RealtimeEvent[]> {
  const payload = await requestJson<unknown>(`/api/v1/events?after_id=${afterId}&limit=500`, signal)
  if (!Array.isArray(payload) || !payload.every(isApiEvent)) throw invalidResponse()
  return payload.map(mapEvent)
}

export async function fetchLatestEventId(signal?: AbortSignal): Promise<number> {
  const payload = await requestJson<unknown>('/api/v1/events/latest', signal)
  if (!isRecord(payload) || !Number.isSafeInteger(payload.last_event_id) || Number(payload.last_event_id) < 0) {
    throw invalidResponse()
  }
  return Number(payload.last_event_id)
}

export function parseEvent(payload: unknown): RealtimeEvent {
  if (!isApiEvent(payload)) throw invalidResponse()
  const event = mapEvent(payload)
  if (event.eventType === 'activity.created' && !isApiActivity(event.payload)) throw invalidResponse()
  if (event.eventType === 'alert.created' && !isApiAlert(event.payload)) throw invalidResponse()
  return event
}

export function activityFromEvent(event: RealtimeEvent): Activity | null {
  return event.eventType === 'activity.created' ? mapActivity(event.payload as unknown as ApiActivity) : null
}

export function alertFromEvent(event: RealtimeEvent): Alert | null {
  return event.eventType === 'alert.created' ? mapAlert(event.payload as unknown as ApiAlert) : null
}
