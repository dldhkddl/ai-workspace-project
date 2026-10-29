import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { employees, teams } from './data/organization'
import { useOfficeData } from './hooks/useOfficeData'

vi.mock('./hooks/useOfficeData', () => ({ useOfficeData: vi.fn() }))
vi.mock('./api/approvals', async () => {
  const actual = await vi.importActual<typeof import('./api/approvals')>('./api/approvals')
  return { ...actual, listApprovals: vi.fn().mockResolvedValue([]), getApproval: vi.fn() }
})
vi.mock('./api/tasks', async () => {
  const actual = await vi.importActual<typeof import('./api/tasks')>('./api/tasks')
  return { ...actual, listTasks: vi.fn().mockResolvedValue([]), getEmployeeTask: vi.fn() }
})
vi.mock('./hooks/useOperationsFeed', () => ({
  useOperationsFeed: () => ({
    activities: [], alerts: [], connectionStatus: 'live', error: null, retry: vi.fn(),
  }),
}))

const mockedUseOfficeData = vi.mocked(useOfficeData)

beforeEach(() => {
  mockedUseOfficeData.mockReturnValue({
    status: 'success',
    data: { teams, employees },
    error: null,
    retry: vi.fn(),
  })
})

describe('AI 운영센터 사무실', () => {
  it('하나의 열린 사무실에 중앙 PM 한 자리와 26명의 직원을 표시한다', () => {
    render(<App />)

    const openOffice = screen.getByRole('region', { name: 'AI 직원 열린 사무실' })
    expect(openOffice).toBeInTheDocument()
    expect(within(openOffice).getAllByRole('heading', { level: 2 }).slice(0, 3).map((heading) => heading.textContent))
      .toEqual(['기존 프로그램 운영팀', 'AI 스토리 제작팀', '총괄·프로젝트팀'])
    expect(screen.queryByRole('region', { name: /팀 사무실$/ })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /직원 선택, 준비 중$/ })).toHaveLength(26)
    expect(screen.getAllByTestId('employee-character')).toHaveLength(26)
    expect(screen.getAllByText('PM 자리')).toHaveLength(1)
    expect(screen.getByRole('button', { name: '스마트스토어 운영 직원 선택, 준비 중' }))
      .toHaveStyle({ '--team-color': '#059669' })
    expect(screen.getByText('0명 연결 · 26명 준비 중')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '시나리오 작가 직원 선택, 준비 중' })).toBeInTheDocument()
  })

  it('팀을 선택하면 해당 팀 직원만 보여준다', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '개발실만 보기' }))

    expect(screen.getByRole('region', { name: 'AI 직원 열린 사무실' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /직원 선택, 준비 중$/ })).toHaveLength(7)
    expect(screen.getByRole('button', { name: 'PM 직원 선택, 준비 중' })).toBeInTheDocument()
  })

  it('직원을 선택하면 연동 전 상세와 비활성 기능을 보여준다', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '스마트스토어 운영 직원 선택, 준비 중' }))

    const detail = screen.getByRole('dialog', { name: '스마트스토어 운영' })
    expect(document.querySelector('.workspace')).not.toHaveClass('workspace--detail-open')
    expect(within(detail).getByRole('heading', { name: '스마트스토어 운영' })).toBeInTheDocument()
    expect(within(detail).getByText('예정 연결 대상')).toBeInTheDocument()
    expect(within(detail).getByText('smartstore-ai')).toBeInTheDocument()
    expect(within(detail).getByText('사용 가능 기능 없음')).toBeInTheDocument()
    expect(within(detail).getByRole('button', { name: '질문하기' })).toBeDisabled()
    expect(within(detail).getByRole('button', { name: '작업 요청' })).toBeDisabled()
  })

  it('상단 메뉴를 전환하고 실제 활동이 없다는 빈 상태를 보여준다', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText('아직 연결된 활동이 없습니다')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '현황판' }))

    expect(screen.getByRole('heading', { name: '현황판' })).toBeInTheDocument()
    expect(screen.getByText('아직 연결된 지표가 없습니다')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'AI 직원 열린 사무실' })).not.toBeInTheDocument()
  })

  it('선택한 직원과 다른 팀을 필터링하면 숨겨진 상세를 닫는다', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '스마트스토어 운영 직원 선택, 준비 중' }))
    expect(screen.getByRole('dialog', { name: '스마트스토어 운영' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '개발실만 보기' }))
    expect(screen.queryByRole('dialog', { name: '스마트스토어 운영' })).not.toBeInTheDocument()
  })

  it('상세 패널은 Escape로 닫히고 선택했던 직원에게 초점이 돌아간다', async () => {
    const user = userEvent.setup()
    render(<App />)
    const employeeButton = screen.getByRole('button', { name: '스마트스토어 운영 직원 선택, 준비 중' })

    await user.click(employeeButton)
    expect(screen.getByRole('button', { name: '직원 상세 닫기' })).toHaveFocus()
    await user.keyboard('{Escape}')

    expect(screen.queryByRole('dialog', { name: '스마트스토어 운영' })).not.toBeInTheDocument()
    expect(employeeButton).toHaveFocus()
  })

  it('개발용 동작 미리보기 없이 실제 업무 상태만 표시한다', () => {
    render(<App />)

    expect(screen.queryByText('개발 전용 · 백엔드 연결 전 제거')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /동작 미리보기/ })).not.toBeInTheDocument()
    const employeeButton = screen.getByRole('button', { name: '스마트스토어 운영 직원 선택, 준비 중' })
    expect(employeeButton).toHaveAttribute('data-character-state', 'seated')
    expect(employees.find((employee) => employee.id === 'smartstore-operator')?.work.status).toBeNull()
  })

  it('API의 실제 작업 중 상태를 캐릭터와 상태 문구에 반영한다', () => {
    mockedUseOfficeData.mockReturnValue({
      status: 'success',
      data: {
        teams,
        employees: employees.map((employee) => employee.id === 'smartstore-operator'
          ? { ...employee, work: { ...employee.work, status: 'working' } }
          : employee),
      },
      error: null,
      retry: vi.fn(),
    })

    render(<App />)

    expect(screen.getByRole('button', { name: /스마트스토어 운영 직원 선택.+작업 중/ }))
      .toHaveAttribute('data-character-state', 'walking')
    expect(screen.getByText('작업 중')).toBeInTheDocument()
  })

  it('전체 캐릭터 애니메이션 일시정지 기능을 유지한다', async () => {
    const user = userEvent.setup()
    render(<App />)
    const pauseButton = screen.getByRole('button', { name: '캐릭터 동작 일시정지' })
    const officeFloor = screen.getByRole('region', { name: 'AI 직원 열린 사무실' })

    expect(pauseButton).toHaveAttribute('aria-pressed', 'false')
    expect(officeFloor).not.toHaveClass('open-office--paused')
    await user.click(pauseButton)

    expect(screen.getByRole('button', { name: '캐릭터 동작 재생' })).toHaveAttribute('aria-pressed', 'true')
    expect(officeFloor).toHaveClass('open-office--paused')
  })

  it('작업 메뉴에서 작업 기록 화면을 연다', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '작업' }))

    expect(screen.getByRole('heading', { name: /작업 기록/ })).toBeInTheDocument()
    expect(await screen.findByText('조건에 맞는 작업이 없습니다.')).toBeInTheDocument()
  })

  it('승인 메뉴에서 승인 화면을 연다', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '승인' }))

    expect(screen.getByRole('heading', { name: /승인/ })).toBeInTheDocument()
    expect(await screen.findByText('조건에 맞는 승인 요청이 없습니다.')).toBeInTheDocument()
  })

  it.each([
    ['보고서', '생성된 운영 보고서가 없습니다'],
    ['연결 관리', '아직 연결된 프로그램이 없습니다'],
  ])('%s 메뉴에 실제 데이터가 없다는 안내를 표시한다', async (menu, message) => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: menu }))
    expect(screen.getByText(message)).toBeInTheDocument()
  })
})
