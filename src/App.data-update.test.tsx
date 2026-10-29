import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { employees, teams } from './data/organization'
import { useOfficeData } from './hooks/useOfficeData'

vi.mock('./hooks/useOfficeData', () => ({ useOfficeData: vi.fn() }))

const mockedUseOfficeData = vi.mocked(useOfficeData)

beforeEach(() => {
  mockedUseOfficeData.mockReturnValue({
    status: 'success',
    data: { teams, employees },
    error: null,
    retry: vi.fn(),
  })
})

describe('App API 데이터 갱신', () => {
  it('선택한 직원의 같은 ID 데이터가 바뀌면 상세 화면도 갱신한다', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<App />)
    await user.click(screen.getByRole('button', { name: /스마트스토어 운영 직원 선택/ }))

    mockedUseOfficeData.mockReturnValue({
      status: 'success',
      data: {
        teams,
        employees: employees.map((employee) => employee.id === 'smartstore-operator'
          ? { ...employee, name: '스마트스토어 운영 갱신' }
          : employee),
      },
      error: null,
      retry: vi.fn(),
    })
    rerender(<App />)

    expect(screen.getByRole('dialog', { name: '스마트스토어 운영 갱신' })).toBeInTheDocument()
  })

  it('선택한 팀이 새 데이터에서 사라지면 전체 보기로 돌아간다', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<App />)
    await user.click(screen.getByRole('button', { name: '개발실만 보기' }))

    mockedUseOfficeData.mockReturnValue({
      status: 'success',
      data: {
        teams: teams.filter((team) => team.id !== 'development'),
        employees: employees.filter((employee) => employee.teamId !== 'development'),
      },
      error: null,
      retry: vi.fn(),
    })
    rerender(<App />)

    await waitFor(() => expect(screen.getByRole('button', { name: '전체 보기' })).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.getAllByRole('button', { name: /직원 선택/ })).toHaveLength(20)
  })
})
