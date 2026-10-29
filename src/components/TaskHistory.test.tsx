import { act } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { getEmployeeTask, listTasks, TaskApiError, type TaskSummary } from '../api/tasks'
import { employees } from '../data/organization'
import { TaskHistory } from './TaskHistory'

vi.mock('../api/tasks', async () => {
  const actual = await vi.importActual<typeof import('../api/tasks')>('../api/tasks')
  return { ...actual, listTasks: vi.fn(), getEmployeeTask: vi.fn() }
})

const mockedList = vi.mocked(listTasks)
const mockedGet = vi.mocked(getEmployeeTask)

const summary = (id: string, overrides: Partial<TaskSummary> = {}): TaskSummary => ({
  id,
  employee_id: 'frontend',
  task_type: 'employee_task',
  title: `작업 ${id}`,
  status: 'completed',
  attempt_count: 1,
  max_attempts: 3,
  started_at: '2026-08-27T00:00:00Z',
  finished_at: '2026-08-27T00:01:00Z',
  created_at: '2026-08-27T00:00:00Z',
  ...overrides,
})

beforeEach(() => {
  mockedList.mockReset()
  mockedGet.mockReset()
})

it('항목을 선택하면 상세를 불러오고 다시 열 때는 캐시를 쓴다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary('t1'), summary('t2')])
  mockedGet.mockResolvedValue({
    id: 't1', employee_id: 'frontend', task_type: 'employee_task', title: '작업 t1',
    status: 'completed',
    result: { response: '작업을 마쳤습니다.', changed_files: ['src/App.tsx'], requires_approval: true },
    error: null, will_retry: false,
  })

  render(<TaskHistory employees={employees} />)
  const row = await screen.findByRole('button', { name: /작업 t1/ })

  await user.click(row)

  expect(await screen.findByText('작업을 마쳤습니다.')).toBeInTheDocument()
  expect(screen.getByText('src/App.tsx')).toBeInTheDocument()
  expect(mockedGet).toHaveBeenCalledTimes(1)

  await user.click(screen.getByRole('button', { name: /작업 t1/ }))
  await user.click(screen.getByRole('button', { name: /작업 t1/ }))

  expect(await screen.findByText('작업을 마쳤습니다.')).toBeInTheDocument()
  expect(mockedGet).toHaveBeenCalledTimes(1)
})

it('작업을 선택하면 아래에 열린 상세 위치로 이동한다', async () => {
  const user = userEvent.setup()
  const scrollIntoView = vi.fn()
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  })
  mockedList.mockResolvedValue([summary('obsidian', { title: '옵시디언' })])
  mockedGet.mockResolvedValue({
    id: 'obsidian', employee_id: 'project-manager', task_type: 'project_plan', title: '옵시디언',
    status: 'completed', result: null, error: null, will_retry: false,
  })

  render(<TaskHistory employees={employees} />)
  await user.click(await screen.findByRole('button', { name: /옵시디언/ }))

  await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' }))
})

it('실패한 작업은 실패 사유를 보여준다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary('t9', { status: 'failed', attempt_count: 3 })])
  mockedGet.mockResolvedValue({
    id: 't9', employee_id: 'frontend', task_type: 'employee_task', title: '작업 t9',
    status: 'failed', result: null,
    error: { code: 'CODEX_NOT_AUTHENTICATED', message: 'Codex 로그인이 필요합니다.' },
    will_retry: false,
  })

  render(<TaskHistory employees={employees} />)
  await user.click(await screen.findByRole('button', { name: /작업 t9/ }))

  expect(await screen.findByText(/Codex 로그인이 필요합니다./)).toBeInTheDocument()
  expect(screen.getByText('재시도 2회')).toBeInTheDocument()
})

it('PM 계획 생성은 끝났어도 질문이 남아 있으면 완료 대신 답변 필요로 표시한다', async () => {
  mockedList.mockResolvedValue([summary('pm-question', {
    employee_id: 'project-manager',
    task_type: 'project_plan',
    title: '옵시디언',
    requires_user_response: true,
  })])

  render(<TaskHistory employees={employees} />)

  const row = await screen.findByRole('button', { name: /옵시디언/ })
  expect(within(row).getByText('답변 필요')).toBeInTheDocument()
  expect(within(row).queryByText('완료')).not.toBeInTheDocument()
})

it('상태 필터를 바꾸면 조건을 붙여 다시 요청한다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([])

  render(<TaskHistory employees={employees} />)
  await screen.findByText('조건에 맞는 작업이 없습니다.')

  await user.selectOptions(screen.getByLabelText('상태'), 'failed')

  await waitFor(() => {
    expect(mockedList).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'failed', offset: 0 }))
  })
})

it('목록을 불러오지 못하면 다시 시도할 수 있다', async () => {
  const user = userEvent.setup()
  mockedList
    .mockRejectedValueOnce(new TaskApiError('작업 서버에 연결할 수 없습니다.'))
    .mockResolvedValue([summary('t1')])

  render(<TaskHistory employees={employees} />)
  expect(await screen.findByText('작업 서버에 연결할 수 없습니다.')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /다시 시도/ }))

  expect(await screen.findByRole('button', { name: /작업 t1/ })).toBeInTheDocument()
})

it('한 페이지가 가득 차면 더 보기로 다음 페이지를 이어 붙인다', async () => {
  const user = userEvent.setup()
  mockedList
    .mockResolvedValueOnce(Array.from({ length: 50 }, (_, index) => summary(`a${index}`)))
    .mockResolvedValueOnce([summary('b0')])

  render(<TaskHistory employees={employees} />)
  await screen.findByRole('button', { name: /작업 a0/ })

  await user.click(screen.getByRole('button', { name: '더 보기' }))

  expect(await screen.findByRole('button', { name: /작업 b0/ })).toBeInTheDocument()
  expect(mockedList).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }))
  expect(screen.queryByRole('button', { name: '더 보기' })).not.toBeInTheDocument()
})

it('진행 중인 작업이 있을 때만 목록을 주기적으로 새로고침한다', async () => {
  vi.useFakeTimers()
  try {
    mockedList.mockResolvedValue([summary('t1', { status: 'running', finished_at: null })])
    render(<TaskHistory employees={employees} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(mockedList).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(mockedList).toHaveBeenCalledTimes(2)

    mockedList.mockResolvedValue([summary('t1')])
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    const callsAfterCompletion = mockedList.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(9000) })
    expect(mockedList).toHaveBeenCalledTimes(callsAfterCompletion)
  } finally {
    vi.useRealTimers()
  }
})

it('별도 확인이 필요한 파일과 변경 없음 결과를 구분해 보여준다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary('s1'), summary('s2')])
  mockedGet.mockImplementation(async (taskId: string) => taskId === 's1'
    ? {
      id: 's1', employee_id: 'frontend', task_type: 'employee_task', title: '작업 s1',
      status: 'completed' as const,
      result: {
        kind: 'changes', response: '고쳤습니다.',
        changed_files: ['AI-company/package.json'],
        sensitive_files: ['AI-company/package.json'],
        requires_approval: true,
      },
      error: null, will_retry: false,
    }
    : {
      id: 's2', employee_id: 'frontend', task_type: 'employee_task', title: '작업 s2',
      status: 'completed' as const,
      result: { kind: 'no_changes', response: '고칠 것이 없었습니다.', requires_approval: false },
      error: null, will_retry: false,
    })

  render(<TaskHistory employees={employees} />)

  await user.click(await screen.findByRole('button', { name: /작업 s1/ }))
  expect(await screen.findByText('별도 확인이 필요한 파일')).toBeInTheDocument()
  expect(screen.getByText(/승인 전에 내용을 직접 확인하세요/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /작업 s2/ }))
  expect(await screen.findByText('바뀐 파일이 없어 승인할 내용이 없습니다.')).toBeInTheDocument()
  expect(screen.queryByText('별도 확인이 필요한 파일')).not.toBeInTheDocument()
})

it('검사하지 못한 파일은 사유를 나눠 보여준다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary('k1')])
  mockedGet.mockResolvedValue({
    id: 'k1', employee_id: 'frontend', task_type: 'employee_task', title: '작업 k1',
    status: 'completed',
    result: {
      kind: 'changes', response: '고쳤습니다.',
      changed_files: ['AI-company/package.json', 'AI-company/그림.png'],
      sensitive_files: ['AI-company/package.json', 'AI-company/그림.png'],
      secret_scan_skipped_files: ['AI-company/그림.png'],
      requires_approval: true,
    },
    error: null, will_retry: false,
  })

  render(<TaskHistory employees={employees} />)
  await user.click(await screen.findByRole('button', { name: /작업 k1/ }))

  const sensitive = (await screen.findByText('별도 확인이 필요한 파일')).parentElement!
  const unscanned = screen.getByText('비밀정보 검사를 못 한 파일').parentElement!
  expect(within(sensitive).getByText('AI-company/package.json')).toBeInTheDocument()
  expect(within(sensitive).queryByText('AI-company/그림.png')).not.toBeInTheDocument()
  expect(within(unscanned).getByText('AI-company/그림.png')).toBeInTheDocument()
})
