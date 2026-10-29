import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import {
  ApprovalApiError,
  decideApproval,
  getApproval,
  listApprovals,
  type ApprovalDetail,
  type ApprovalSummary,
} from '../api/approvals'
import {
  getEmployeeTask, getProjectWorkflow, listTasks, respondToProjectPlan,
} from '../api/tasks'
import { ApprovalCenter } from './ApprovalCenter'

vi.mock('../api/approvals', async () => {
  const actual = await vi.importActual<typeof import('../api/approvals')>('../api/approvals')
  return { ...actual, listApprovals: vi.fn(), getApproval: vi.fn(), decideApproval: vi.fn() }
})

vi.mock('../api/tasks', async () => {
  const actual = await vi.importActual<typeof import('../api/tasks')>('../api/tasks')
  return {
    ...actual,
    listTasks: vi.fn(),
    getEmployeeTask: vi.fn(),
    getProjectWorkflow: vi.fn(),
    respondToProjectPlan: vi.fn(),
  }
})

const mockedList = vi.mocked(listApprovals)
const mockedGet = vi.mocked(getApproval)
const mockedDecide = vi.mocked(decideApproval)
const mockedListTasks = vi.mocked(listTasks)
const mockedGetTask = vi.mocked(getEmployeeTask)
const mockedGetWorkflow = vi.mocked(getProjectWorkflow)
const mockedRespond = vi.mocked(respondToProjectPlan)

const summary = (overrides: Partial<ApprovalSummary> = {}): ApprovalSummary => ({
  id: 'ap-1', project_id: 'default', task_id: 'task-1', thread_id: 'task-1',
  branch: 'agent/frontend/task-1', status: 'pending', revision: 1,
  result_sha: 'b'.repeat(40), has_sensitive_changes: false,
  expires_at: null, applied_at: null, created_at: '2026-08-28T00:00:00Z',
  ...overrides,
})

const detail = (overrides: Partial<ApprovalDetail> = {}): ApprovalDetail => ({
  ...summary(overrides as Partial<ApprovalSummary>),
  base_sha: 'a'.repeat(40), result_tree: 'c'.repeat(40), impact_hash: 'd'.repeat(64),
  target_branch: 'main', target_sha_at_approval: null, applied_sha: null,
  invalidated_reason: null, last_apply_error: null, apply_attempt: 0, actor_id: null,
  sensitive_files: [], task_title: '화면 수정', employee_id: 'frontend',
  changed_files: ['AI-company/화면.txt'], secret_scan_skipped_files: [],
  diff_stat: '1 file changed', patch_preview: 'diff --git a/화면.txt b/화면.txt',
  patch_preview_truncated: false,
  ...overrides,
})

beforeEach(() => {
  mockedList.mockReset()
  mockedGet.mockReset()
  mockedDecide.mockReset()
  mockedListTasks.mockReset()
  mockedListTasks.mockResolvedValue([])
  mockedGetTask.mockReset()
  mockedGetWorkflow.mockReset()
  mockedRespond.mockReset()
})

it('PM 확인 요청을 승인 화면에 표시하고 답변하면 대기 목록에서 제거한다', async () => {
  const user = userEvent.setup()
  const onPmResponseSubmitted = vi.fn()
  mockedList.mockResolvedValue([])
  mockedListTasks
    .mockResolvedValueOnce([{
      id: 'pm-1', employee_id: 'project-manager', task_type: 'project_plan', title: '옵시디언',
      status: 'completed', attempt_count: 1, max_attempts: 3,
      started_at: '2026-08-28T00:00:00Z', finished_at: '2026-08-28T00:01:00Z',
      created_at: '2026-08-28T00:00:00Z', requires_user_response: true,
    }])
    .mockResolvedValue([])
  mockedGetTask.mockResolvedValue({
    id: 'pm-1', employee_id: 'project-manager', task_type: 'project_plan', title: '옵시디언',
    status: 'completed', error: null, will_retry: false, requires_user_response: true,
    result: { plan: {
      objective: '옵시디언 자동 백업', completion_criteria: [], assignments: [], risks: [],
      needs_user_choice: true,
      user_question: '개인용 비공개 GitHub 자동 백업으로 진행할까요?',
    } },
  })
  mockedGetWorkflow.mockResolvedValue({
    status: 'awaiting_user', plan_task_id: 'pm-1', assignments: [], synthesis: null, continuation: null,
  })
  mockedRespond.mockResolvedValue({
    id: 'pm-2', employee_id: 'project-manager', task_type: 'project_plan', title: '[PM 후속] 옵시디언',
    status: 'queued', result: null, error: null, will_retry: false,
  })

  render(<ApprovalCenter onPmResponseSubmitted={onPmResponseSubmitted} />)

  expect(await screen.findByRole('heading', { name: 'PM 확인 대기 1건' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /옵시디언/ }))
  expect(await screen.findByText(/개인용 비공개 GitHub 자동 백업으로 진행할까요/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'PM 제안대로 진행' }))

  await waitFor(() => expect(mockedListTasks).toHaveBeenCalledTimes(2))
  expect(onPmResponseSubmitted).toHaveBeenCalledWith('pm-1')
  expect(screen.queryByRole('heading', { name: /PM 확인 대기 1건/ })).not.toBeInTheDocument()
})

it('기본은 요약만 보여주고 전체 변경은 눌러야 펼쳐진다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary()])
  mockedGet.mockResolvedValue(detail())

  render(<ApprovalCenter />)
  await user.click(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ }))

  expect(await screen.findByText('1 file changed')).toBeInTheDocument()
  expect(screen.getByText('AI-company/화면.txt')).toBeInTheDocument()
  // 큰 패치를 그냥 뿌리면 화면이 멈춘다. 기본은 접혀 있어야 한다.
  expect(screen.queryByText(/diff --git/)).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '전체 변경 보기' }))
  expect(screen.getByText(/diff --git/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '전체 변경 접기' }))
  expect(screen.queryByText(/diff --git/)).not.toBeInTheDocument()
})

it('잘린 패치는 배지와 안내를 함께 보여준다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary()])
  mockedGet.mockResolvedValue(detail({ patch_preview_truncated: true }))

  render(<ApprovalCenter />)
  await user.click(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ }))
  expect(await screen.findByText('일부만 표시됨')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '전체 변경 보기' }))
  expect(screen.getByText(/변경이 커서 요약만 표시합니다/)).toBeInTheDocument()
})

it('민감 변경은 확인 전까지 승인할 수 없다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary({ has_sensitive_changes: true })])
  mockedGet.mockResolvedValue(detail({
    has_sensitive_changes: true, sensitive_files: ['AI-company/package.json'],
  }))
  mockedDecide.mockResolvedValue(detail({ status: 'approved', has_sensitive_changes: true }))

  render(<ApprovalCenter />)
  await user.click(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ }))
  await screen.findByText('별도 확인이 필요한 파일')

  expect(screen.getByRole('button', { name: '승인' })).toBeDisabled()

  await user.click(screen.getByRole('checkbox'))
  expect(screen.getByRole('button', { name: '승인' })).toBeEnabled()

  await user.click(screen.getByRole('button', { name: '승인' }))
  expect(mockedDecide).toHaveBeenCalledWith('ap-1', 'approve', {
    result_sha: 'b'.repeat(40), revision: 1, acknowledge_sensitive: true,
  })
})

it('승인 직후에는 완료가 아니라 적용 대기로 보여준다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary()])
  mockedGet.mockResolvedValue(detail())
  mockedDecide.mockResolvedValue(detail({ status: 'approved' }))

  render(<ApprovalCenter />)
  await user.click(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ }))
  await user.click(await screen.findByRole('button', { name: '승인' }))

  expect(await screen.findByText(/적용 작업을 기다리는 중입니다/)).toBeInTheDocument()
  // 중복 클릭 방지
  expect(screen.getByRole('button', { name: '승인' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '거절' })).toBeDisabled()
})

it('막힌 승인은 원인과 다시 시도 버튼을 보여준다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary({ status: 'blocked' })])
  mockedGet.mockResolvedValue(detail({
    status: 'blocked',
    last_apply_error: '원본에 저장하지 않은 변경이 있습니다 (다른-프로그램/b.txt)',
  }))
  mockedDecide.mockResolvedValue(detail({ status: 'approved' }))

  render(<ApprovalCenter />)
  await user.click(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ }))

  expect(await screen.findByText(/다른-프로그램\/b\.txt/)).toBeInTheDocument()
  expect(screen.getByText(/커밋하거나 되돌린 뒤 다시 시도하세요/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '다시 시도' }))
  expect(mockedDecide).toHaveBeenCalledWith('ap-1', 'retry', {
    result_sha: 'b'.repeat(40), revision: 1,
  })
})

it('오래된 화면으로 승인하면 오류를 보여주고 현재 내용을 다시 읽는다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary()])
  mockedGet.mockResolvedValue(detail())
  mockedDecide.mockRejectedValue(new ApprovalApiError(
    '승인하려는 결과가 화면에 보이던 것과 다릅니다.', 'APPROVAL_TARGET_MISMATCH', 409,
  ))

  render(<ApprovalCenter />)
  await user.click(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ }))
  await user.click(await screen.findByRole('button', { name: '승인' }))

  expect(await screen.findByText('승인하려는 결과가 화면에 보이던 것과 다릅니다.')).toBeInTheDocument()
  await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2))
})

it('검사하지 못한 파일은 사유를 나눠 보여준다', async () => {
  const user = userEvent.setup()
  mockedList.mockResolvedValue([summary()])
  mockedGet.mockResolvedValue(detail({
    sensitive_files: ['AI-company/package.json'],
    secret_scan_skipped_files: ['AI-company/그림.png'],
  }))

  render(<ApprovalCenter />)
  await user.click(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ }))

  const sensitive = (await screen.findByText('별도 확인이 필요한 파일')).parentElement!
  const unscanned = screen.getByText('비밀정보 검사를 못 한 파일').parentElement!
  expect(within(sensitive).getByText('AI-company/package.json')).toBeInTheDocument()
  expect(within(unscanned).getByText('AI-company/그림.png')).toBeInTheDocument()
})

it('목록을 불러오지 못하면 다시 시도할 수 있다', async () => {
  const user = userEvent.setup()
  mockedList
    .mockRejectedValueOnce(new ApprovalApiError('승인 서버에 연결할 수 없습니다.'))
    .mockResolvedValue([summary()])

  render(<ApprovalCenter />)
  expect(await screen.findByText('승인 서버에 연결할 수 없습니다.')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /다시 시도/ }))
  expect(await screen.findByRole('button', { name: /agent\/frontend\/task-1/ })).toBeInTheDocument()
})
