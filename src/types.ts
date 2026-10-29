export type ConnectionStatus =
  | 'not_connected'
  | 'setup_required'
  | 'awaiting_first_sync'
  | 'healthy'
  | 'delayed'
  | 'error'
  | 'stopped'

export type WorkStatus =
  | 'idle'
  | 'working'
  | 'reviewing'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | null

export interface EmployeeConnection {
  status: ConnectionStatus
  source: string | null
  lastCheckedAt: string | null
  lastSuccessAt: string | null
  error: { code: string; message: string } | null
}

export interface EmployeeWork {
  status: WorkStatus
  taskId: string | null
  title: string | null
  stage: string | null
  progress: number | null
}

export interface Employee {
  id: string
  name: string
  teamId: string
  role: string
  icon: string
  connection: EmployeeConnection
  work: EmployeeWork
  summary: string | null
  alerts: string[]
  allowedTools: string[]
  approvalRequired: boolean
  plannedSource: string
  plannedFeatures: string[]
}

export interface Team {
  id: string
  name: string
  shortName: string
  color: string
  employeeIds: string[]
}

export interface Activity {
  id: string
  employeeId: string | null
  taskId: string | null
  eventType: string
  title: string
  detail: Record<string, unknown>
  correlationId: string | null
  createdAt: string
}

export interface Alert {
  id: string
  employeeId: string | null
  taskId: string | null
  severity: 'info' | 'warning' | 'error'
  code: string
  message: string
  detail: Record<string, unknown>
  correlationId: string | null
  resolvedAt: string | null
  createdAt: string
}

export interface RealtimeEvent {
  id: number
  eventType: 'activity.created' | 'alert.created' | string
  resourceType: string
  resourceId: string
  payload: Record<string, unknown>
  correlationId: string | null
  createdAt: string
}
