import type { ConnectionStatus, Employee } from './types'

export const connectionStatusLabels: Record<ConnectionStatus, string> = {
  not_connected: '준비 중',
  setup_required: '설정 필요',
  awaiting_first_sync: '수집 대기',
  healthy: '연결됨',
  delayed: '지연',
  error: '오류',
  stopped: '중지',
}

export function getConnectionStatusLabel(status: ConnectionStatus) {
  return connectionStatusLabels[status]
}

export function formatConnectionSummary(employees: Employee[]) {
  const counts = employees.reduce<Record<ConnectionStatus, number>>((summary, employee) => {
    summary[employee.connection.status] += 1
    return summary
  }, {
    not_connected: 0,
    setup_required: 0,
    awaiting_first_sync: 0,
    healthy: 0,
    delayed: 0,
    error: 0,
    stopped: 0,
  })
  const parts = [`${counts.healthy}명 연결`, `${counts.not_connected}명 준비 중`]
  const additionalStatuses: ConnectionStatus[] = [
    'setup_required',
    'awaiting_first_sync',
    'delayed',
    'error',
    'stopped',
  ]

  additionalStatuses.forEach((status) => {
    if (counts[status] > 0) parts.push(`${counts[status]}명 ${connectionStatusLabels[status]}`)
  })
  return parts.join(' · ')
}
