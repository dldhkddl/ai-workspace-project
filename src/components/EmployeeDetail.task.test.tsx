import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { employees, teams } from '../data/organization'
import { createEmployeeTask, runEmployeeTask } from '../api/tasks'
import { EmployeeDetail } from './EmployeeDetail'

vi.mock('../api/tasks', async () => {
  const actual = await vi.importActual<typeof import('../api/tasks')>('../api/tasks')
  return { ...actual, createEmployeeTask: vi.fn(), runEmployeeTask: vi.fn() }
})

const mockedCreate = vi.mocked(createEmployeeTask)
const mockedRun = vi.mocked(runEmployeeTask)

beforeEach(() => {
  mockedCreate.mockReset()
  mockedRun.mockReset()
})

it('작업 요청이 접수되면 직원 창을 닫고 사무실 진행 화면으로 돌아간다', async () => {
  const user = userEvent.setup()
  const source = employees.find((item) => item.id === 'research-factcheck')!
  const employee = { ...source, connection: { ...source.connection, status: 'healthy' as const } }
  const team = teams.find((item) => item.id === employee.teamId)!
  const onClose = vi.fn()
  mockedCreate.mockResolvedValue({
    id: 'task-1', employee_id: employee.id, task_type: 'employee_task', title: '자료 확인',
    status: 'queued', result: null,
    error: null, will_retry: false,
  })
  mockedRun.mockImplementation(() => new Promise(() => {}))

  render(<EmployeeDetail employee={employee} team={team} onClose={onClose} />)
  await user.click(screen.getByRole('button', { name: '작업 요청' }))
  await user.type(screen.getByLabelText('작업 제목'), '자료 확인')
  await user.type(screen.getByLabelText('요청 내용'), '이 자료를 검토해 주세요')
  await user.click(screen.getByRole('button', { name: '요청 보내기' }))

  await waitFor(() => expect(mockedCreate).toHaveBeenCalledWith(
    employee.id, '자료 확인', '이 자료를 검토해 주세요', expect.any(String),
  ))
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('프로그램 직원은 작업 요청을 비활성화한다', () => {
  const employee = employees.find((item) => item.id === 'stock-operator')!
  const team = teams.find((item) => item.id === employee.teamId)!
  render(<EmployeeDetail employee={employee} team={team} onClose={vi.fn()} />)
  expect(screen.getByRole('button', { name: '작업 요청' })).toBeDisabled()
})

it('잠금 사유가 있으면 고정 문구 대신 실제 사유를 보여준다', () => {
  const source = employees.find((item) => item.id === 'frontend')!
  const employee = {
    ...source,
    connection: {
      ...source.connection,
      status: 'stopped' as const,
      error: {
        code: 'DEVELOPMENT_MODE_PAUSED',
        message: '안전 구조 개선 중이라 개발 작업을 잠시 중단했습니다.',
      },
    },
  }
  const team = teams.find((item) => item.id === employee.teamId)!

  render(<EmployeeDetail employee={employee} team={team} onClose={vi.fn()} />)

  expect(screen.getByRole('button', { name: '작업 요청' })).toBeDisabled()
  expect(screen.getByText('안전 구조 개선 중이라 개발 작업을 잠시 중단했습니다.')).toBeInTheDocument()
  expect(screen.queryByText('현재 연결 준비 중인 직원입니다.')).not.toBeInTheDocument()
})

it('사유가 없으면 기본 안내 문구를 유지한다', () => {
  const employee = employees.find((item) => item.id === 'stock-operator')!
  const team = teams.find((item) => item.id === employee.teamId)!

  render(<EmployeeDetail employee={employee} team={team} onClose={vi.fn()} />)

  expect(screen.getByText('현재 연결 준비 중인 직원입니다.')).toBeInTheDocument()
})
