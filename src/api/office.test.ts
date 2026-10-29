import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchOffice, OfficeApiError } from './office'

const apiOffice = {
  teams: [{ id: 'operations', name: '기존 프로그램 운영팀', short_name: '운영팀', color: '#059669', employee_ids: ['smartstore-operator'] }],
  employees: [{
    id: 'smartstore-operator',
    name: '스마트스토어 운영',
    team_id: 'operations',
    role: '주문·취소·반품·배송·재고 현황',
    icon: 'Store',
    summary: null,
    allowed_tools: [],
    approval_required: true,
    planned_source: 'smartstore-ai',
    planned_features: ['주문 조회'],
    alerts: [],
    connection: { status: 'not_connected', source: null, last_checked_at: null, last_success_at: null, error: null },
    work: { status: null, task_id: null, title: null, stage: null, progress: null },
  }],
}

afterEach(() => vi.unstubAllGlobals())

describe('office API client', () => {
  it('백엔드 snake_case 응답을 화면용 camelCase 데이터로 변환한다', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(apiOffice), { status: 200 })))

    const result = await fetchOffice()

    expect(result.teams[0]).toMatchObject({ shortName: '운영팀', employeeIds: ['smartstore-operator'] })
    expect(result.employees[0]).toMatchObject({
      teamId: 'operations',
      plannedSource: 'smartstore-ai',
      approvalRequired: true,
      connection: { lastCheckedAt: null, lastSuccessAt: null },
      work: { taskId: null },
    })
  })

  it('AbortSignal을 fetch에 전달한다', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(apiOffice), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await fetchOffice(controller.signal)

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/office', { signal: controller.signal })
  })

  it('네트워크 실패를 안전한 오류로 변환한다', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('private network detail')))

    await expect(fetchOffice()).rejects.toEqual(
      new OfficeApiError('OFFICE_CONNECTION_FAILED', '운영센터 서버에 연결할 수 없습니다.'),
    )
  })

  it('HTTP 실패를 안전한 오류로 변환한다', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 503 })))

    await expect(fetchOffice()).rejects.toEqual(
      new OfficeApiError('OFFICE_REQUEST_FAILED', '운영센터 데이터를 불러오지 못했습니다.'),
    )
  })

  it('잘못된 JSON 응답을 거부한다', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{', { status: 200 })))

    await expect(fetchOffice()).rejects.toMatchObject({ code: 'OFFICE_INVALID_RESPONSE' })
  })

  it('필수 배열이 없는 응답을 거부한다', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 200 })))

    await expect(fetchOffice()).rejects.toMatchObject({ code: 'OFFICE_INVALID_RESPONSE' })
  })

  it('중첩 필드가 누락된 직원을 거부한다', async () => {
    const invalid = structuredClone(apiOffice)
    Reflect.deleteProperty(invalid.employees[0], 'connection')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(invalid), { status: 200 })))

    await expect(fetchOffice()).rejects.toMatchObject({ code: 'OFFICE_INVALID_RESPONSE' })
  })

  it('알 수 없는 상태 값을 거부한다', async () => {
    const invalid = structuredClone(apiOffice)
    invalid.employees[0].connection.status = 'mystery'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(invalid), { status: 200 })))

    await expect(fetchOffice()).rejects.toMatchObject({ code: 'OFFICE_INVALID_RESPONSE' })
  })
})
