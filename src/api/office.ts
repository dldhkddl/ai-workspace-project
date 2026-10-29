import type { ConnectionStatus, Employee, Team, WorkStatus } from '../types'

export interface OfficeData {
  teams: Team[]
  employees: Employee[]
}

type OfficeErrorCode = 'OFFICE_CONNECTION_FAILED' | 'OFFICE_REQUEST_FAILED' | 'OFFICE_INVALID_RESPONSE'

export class OfficeApiError extends Error {
  constructor(public readonly code: OfficeErrorCode, message: string) {
    super(message)
    this.name = 'OfficeApiError'
  }
}

interface ApiTeam {
  id: string
  name: string
  short_name: string
  color: string
  employee_ids: string[]
}

interface ApiEmployee {
  id: string
  name: string
  team_id: string
  role: string
  icon: string
  summary: string | null
  allowed_tools: string[]
  approval_required: boolean
  planned_source: string
  planned_features: string[]
  alerts: string[]
  connection: {
    status: ConnectionStatus
    source: string | null
    last_checked_at: string | null
    last_success_at: string | null
    error: { code: string; message: string } | null
  }
  work: {
    status: WorkStatus
    task_id: string | null
    title: string | null
    stage: string | null
    progress: number | null
  }
}

interface ApiOffice {
  teams: ApiTeam[]
  employees: ApiEmployee[]
}

const connectionStatuses = new Set<ConnectionStatus>([
  'not_connected', 'setup_required', 'awaiting_first_sync', 'healthy', 'delayed', 'error', 'stopped',
])
const workStatuses = new Set<Exclude<WorkStatus, null>>([
  'idle', 'working', 'reviewing', 'awaiting_approval', 'completed', 'failed',
])

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isString = (value: unknown): value is string => typeof value === 'string'
const isNullableString = (value: unknown): value is string | null => value === null || isString(value)
const isStringArray = (value: unknown): value is string[] => Array.isArray(value) && value.every(isString)

const isApiTeam = (value: unknown): value is ApiTeam => isRecord(value)
  && isString(value.id)
  && isString(value.name)
  && isString(value.short_name)
  && isString(value.color)
  && isStringArray(value.employee_ids)

const isApiError = (value: unknown): value is ApiEmployee['connection']['error'] => value === null
  || (isRecord(value) && isString(value.code) && isString(value.message))

const isApiConnection = (value: unknown): value is ApiEmployee['connection'] => isRecord(value)
  && isString(value.status)
  && connectionStatuses.has(value.status as ConnectionStatus)
  && isNullableString(value.source)
  && isNullableString(value.last_checked_at)
  && isNullableString(value.last_success_at)
  && isApiError(value.error)

const isApiWork = (value: unknown): value is ApiEmployee['work'] => isRecord(value)
  && (value.status === null || (isString(value.status) && workStatuses.has(value.status as Exclude<WorkStatus, null>)))
  && isNullableString(value.task_id)
  && isNullableString(value.title)
  && isNullableString(value.stage)
  && (value.progress === null || typeof value.progress === 'number')

const isApiEmployee = (value: unknown): value is ApiEmployee => isRecord(value)
  && isString(value.id)
  && isString(value.name)
  && isString(value.team_id)
  && isString(value.role)
  && isString(value.icon)
  && isNullableString(value.summary)
  && isStringArray(value.allowed_tools)
  && typeof value.approval_required === 'boolean'
  && isString(value.planned_source)
  && isStringArray(value.planned_features)
  && isStringArray(value.alerts)
  && isApiConnection(value.connection)
  && isApiWork(value.work)

const isApiOffice = (value: unknown): value is ApiOffice => isRecord(value)
  && Array.isArray(value.teams)
  && value.teams.every(isApiTeam)
  && Array.isArray(value.employees)
  && value.employees.every(isApiEmployee)

const mapTeam = (team: ApiTeam): Team => ({
  id: team.id,
  name: team.name,
  shortName: team.short_name,
  color: team.color,
  employeeIds: team.employee_ids,
})

const mapEmployee = (employee: ApiEmployee): Employee => ({
  id: employee.id,
  name: employee.name,
  teamId: employee.team_id,
  role: employee.role,
  icon: employee.icon,
  summary: employee.summary,
  alerts: employee.alerts,
  allowedTools: employee.allowed_tools,
  approvalRequired: employee.approval_required,
  plannedSource: employee.planned_source,
  plannedFeatures: employee.planned_features,
  connection: {
    status: employee.connection.status,
    source: employee.connection.source,
    lastCheckedAt: employee.connection.last_checked_at,
    lastSuccessAt: employee.connection.last_success_at,
    error: employee.connection.error,
  },
  work: {
    status: employee.work.status,
    taskId: employee.work.task_id,
    title: employee.work.title,
    stage: employee.work.stage,
    progress: employee.work.progress,
  },
})

const invalidResponse = () => new OfficeApiError(
  'OFFICE_INVALID_RESPONSE',
  '운영센터 데이터 형식이 올바르지 않습니다.',
)

export async function fetchOffice(signal?: AbortSignal): Promise<OfficeData> {
  let response: Response
  try {
    response = await fetch('/api/v1/office', { signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new OfficeApiError('OFFICE_CONNECTION_FAILED', '운영센터 서버에 연결할 수 없습니다.')
  }

  if (!response.ok) {
    throw new OfficeApiError('OFFICE_REQUEST_FAILED', '운영센터 데이터를 불러오지 못했습니다.')
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw invalidResponse()
  }

  if (!isApiOffice(payload)) throw invalidResponse()

  return {
    teams: payload.teams.map(mapTeam),
    employees: payload.employees.map(mapEmployee),
  }
}
