import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { employees, teams } from './data/organization'

vi.mock('./hooks/useOperationsFeed', () => ({
  useOperationsFeed: () => ({
    activities: [], alerts: [], connectionStatus: 'live', error: null, retry: vi.fn(),
  }),
}))

const apiOffice = {
  teams: teams.map((team) => ({
    id: team.id,
    name: team.name,
    short_name: team.shortName,
    color: team.color,
    employee_ids: team.employeeIds,
  })),
  employees: employees.map((employee) => ({
    id: employee.id,
    name: employee.name,
    team_id: employee.teamId,
    role: employee.role,
    icon: employee.icon,
    summary: employee.summary,
    allowed_tools: employee.allowedTools,
    approval_required: employee.approvalRequired,
    planned_source: employee.plannedSource,
    planned_features: employee.plannedFeatures,
    alerts: employee.alerts,
    connection: {
      status: employee.connection.status,
      source: employee.connection.source,
      last_checked_at: employee.connection.lastCheckedAt,
      last_success_at: employee.connection.lastSuccessAt,
      error: employee.connection.error,
    },
    work: {
      status: employee.work.status,
      task_id: employee.work.taskId,
      title: employee.work.title,
      stage: employee.work.stage,
      progress: employee.work.progress,
    },
  })),
}

afterEach(() => vi.unstubAllGlobals())

describe('App 운영센터 API 연결 상태', () => {
  it('데이터를 받기 전에는 연결 중 화면만 표시한다', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    render(<App />)

    expect(screen.getByRole('status')).toHaveTextContent('운영센터 연결 중')
    expect(screen.queryByRole('region', { name: /AI 직원/ })).not.toBeInTheDocument()
  })

  it('실패 후 다시 시도하면 실제 API 경로를 거쳐 사무실 화면으로 복구한다', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('network failed'))
      .mockResolvedValueOnce(new Response(JSON.stringify(apiOffice), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('운영센터 서버에 연결할 수 없습니다.')
    await user.click(screen.getByRole('button', { name: '다시 시도' }))

    expect(await screen.findByRole('region', { name: 'AI 직원 열린 사무실' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /스마트스토어 운영 직원 선택/ })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
