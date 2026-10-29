import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { dispatchProjectPlan, getProjectWorkflow, respondToProjectPlan, TaskApiError } from '../api/tasks'
import { ProjectWorkflowPanel } from './ProjectWorkflowPanel'

vi.mock('../api/tasks', async () => {
  const actual = await vi.importActual<typeof import('../api/tasks')>('../api/tasks')
  return {
    ...actual,
    dispatchProjectPlan: vi.fn(),
    getProjectWorkflow: vi.fn(),
    respondToProjectPlan: vi.fn(),
  }
})

const mockedGet = vi.mocked(getProjectWorkflow)
const mockedDispatch = vi.mocked(dispatchProjectPlan)
const mockedRespond = vi.mocked(respondToProjectPlan)
const planResult = {
  plan: {
    objective: '신제품 출시 계획을 완성한다.',
    completion_criteria: ['시장 근거와 실행안이 함께 있다.'],
    assignments: [
      { employee_id: 'research-factcheck', task: '시장 조사', expected_output: '조사 결과' },
      { employee_id: 'marketing-lead', task: '출시 전략', expected_output: '실행안' },
    ],
    risks: ['자료 부족'],
    needs_user_choice: false,
    user_question: null,
  },
}

beforeEach(() => {
  mockedGet.mockReset()
  mockedDispatch.mockReset()
  mockedRespond.mockReset()
})

afterEach(() => vi.useRealTimers())

it('PM 계획을 보여주고 사용자 승인 후 직원 작업을 배분한다', async () => {
  const user = userEvent.setup()
  mockedGet.mockResolvedValue({
    status: 'not_started', plan_task_id: 'plan-1', assignments: [], synthesis: null,
  })
  mockedDispatch.mockResolvedValue({
    status: 'running', plan_task_id: 'plan-1', synthesis: null,
    assignments: [
      { id: 'a-1', employee_id: 'research-factcheck', task_type: 'employee_task', title: '시장 조사', status: 'queued', attempt_count: 0, max_attempts: 3, started_at: null, finished_at: null, created_at: '2026-08-28T00:00:00Z' },
      { id: 'a-2', employee_id: 'marketing-lead', task_type: 'employee_task', title: '출시 전략', status: 'queued', attempt_count: 0, max_attempts: 3, started_at: null, finished_at: null, created_at: '2026-08-28T00:00:00Z' },
    ],
  })

  render(<ProjectWorkflowPanel
    taskId="plan-1"
    result={planResult}
    employeeNames={new Map([
      ['research-factcheck', '정보조사 담당자'],
      ['marketing-lead', '마케팅 책임자'],
    ])}
  />)

  expect(await screen.findByText('신제품 출시 계획을 완성한다.')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '계획 실행' }))

  expect(mockedDispatch).toHaveBeenCalledWith('plan-1')
  expect(await screen.findByText('직원 작업 진행 중')).toBeInTheDocument()
  expect(screen.getAllByText('정보조사 담당자')).toHaveLength(2)
  expect(screen.getAllByText('마케팅 책임자')).toHaveLength(2)
  expect(screen.queryByText('research-factcheck')).not.toBeInTheDocument()
})

it('PM 종합 보고가 완료되면 결과를 표시한다', async () => {
  mockedGet.mockResolvedValue({
    status: 'completed', plan_task_id: 'plan-1', assignments: [],
    synthesis: {
      id: 'summary-1', employee_id: 'project-manager', task_type: 'project_synthesis', title: '종합',
      status: 'completed', error: null, will_retry: false,
      result: { report: {
        summary: '조사와 전략 수립이 모두 끝났습니다.', completed_items: ['시장 조사', '출시 전략'],
        incomplete_items: [], risks: ['일정 지연'], next_actions: ['최종 승인'],
      } },
    },
  })

  render(<ProjectWorkflowPanel taskId="plan-1" result={planResult} />)

  expect(await screen.findByText('PM 종합 보고')).toBeInTheDocument()
  expect(screen.getByText('조사와 전략 수립이 모두 끝났습니다.')).toBeInTheDocument()
  expect(screen.getByText('최종 승인')).toBeInTheDocument()
})

it('배분 후 직원 결과와 PM 종합 상태를 자동으로 갱신한다', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  mockedGet
    .mockResolvedValueOnce({ status: 'not_started', plan_task_id: 'plan-1', assignments: [], synthesis: null })
    .mockResolvedValueOnce({
      status: 'completed', plan_task_id: 'plan-1', assignments: [],
      synthesis: {
        id: 'summary-1', employee_id: 'project-manager', task_type: 'project_synthesis', title: '종합',
        status: 'completed', error: null, will_retry: false,
        result: { report: { summary: '자동 종합 완료', completed_items: [], incomplete_items: [], risks: [], next_actions: [] } },
      },
    })
  mockedDispatch.mockResolvedValue({
    status: 'running', plan_task_id: 'plan-1', assignments: [], synthesis: null,
  })

  render(<ProjectWorkflowPanel taskId="plan-1" result={planResult} />)
  await screen.findByRole('button', { name: '계획 실행' })
  await user.click(screen.getByRole('button', { name: '계획 실행' }))
  await vi.advanceTimersByTimeAsync(3000)

  expect(await screen.findByText('자동 종합 완료')).toBeInTheDocument()
})

it('PM 확인 질문에 제안대로 답하면 같은 작업에서 후속 계획을 시작한다', async () => {
  const user = userEvent.setup()
  mockedGet.mockResolvedValue({
    status: 'awaiting_user', plan_task_id: 'plan-1', assignments: [], synthesis: null, continuation: null,
  })
  mockedRespond.mockResolvedValue({
    id: 'plan-2', employee_id: 'project-manager', task_type: 'project_plan', title: '후속 계획',
    status: 'queued', result: null, error: null, will_retry: false,
  })
  render(<ProjectWorkflowPanel taskId="plan-1" result={{
    plan: { ...planResult.plan, needs_user_choice: true, user_question: '국내부터 할까요?' },
  }} />)

  expect(await screen.findByText(/국내부터 할까요\?/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'PM 제안대로 진행' }))

  expect(mockedRespond).toHaveBeenCalledWith('plan-1', { action: 'approve', message: null })
  expect(await screen.findByText('PM이 답변을 반영해 계획을 다시 세우고 있습니다.')).toBeInTheDocument()
})

it('PM 확인 질문에 수정 내용을 입력해 보낸다', async () => {
  const user = userEvent.setup()
  mockedGet.mockResolvedValue({
    status: 'awaiting_user', plan_task_id: 'plan-1', assignments: [], synthesis: null, continuation: null,
  })
  mockedRespond.mockResolvedValue({
    id: 'plan-2', employee_id: 'project-manager', task_type: 'project_plan', title: '후속 계획',
    status: 'queued', result: null, error: null, will_retry: false,
  })
  render(<ProjectWorkflowPanel taskId="plan-1" result={{
    plan: { ...planResult.plan, needs_user_choice: true, user_question: '국내부터 할까요?' },
  }} />)

  await screen.findByText(/국내부터 할까요\?/)
  await user.click(screen.getByRole('button', { name: '수정 요청' }))
  await user.type(screen.getByRole('textbox', { name: 'PM에게 보낼 답변' }), '해외부터 조사해 주세요.')
  await user.click(screen.getByRole('button', { name: '답변 보내기' }))

  expect(mockedRespond).toHaveBeenCalledWith('plan-1', {
    action: 'revise', message: '해외부터 조사해 주세요.',
  })
})

it('PM이 만든 구조화된 선택지를 버튼으로 고르게 한다', async () => {
  const user = userEvent.setup()
  mockedGet.mockResolvedValue({
    status: 'awaiting_user', plan_task_id: 'plan-1', assignments: [], synthesis: null, continuation: null,
  })
  mockedRespond.mockResolvedValue({
    id: 'plan-2', employee_id: 'project-manager', task_type: 'project_plan', title: '후속 계획',
    status: 'queued', result: null, error: null, will_retry: false,
  })
  render(<ProjectWorkflowPanel taskId="plan-1" result={{
    plan: {
      ...planResult.plan,
      needs_user_choice: true,
      user_question: '원하는 자동화 범위를 선택해 주세요.',
      user_choices: ['개인용 비공개 GitHub 자동 백업', '여러 기기 양방향 동기화', '웹 발행까지 포함'],
    },
  }} />)

  await user.click(await screen.findByRole('button', { name: '2. 여러 기기 양방향 동기화' }))

  expect(mockedRespond).toHaveBeenCalledWith('plan-1', {
    action: 'revise', message: '여러 기기 양방향 동기화',
  })
  expect(screen.queryByRole('button', { name: 'PM 제안대로 진행' })).not.toBeInTheDocument()
})

it('기존 ①②③ 질문도 선택지 버튼으로 변환한다', async () => {
  mockedGet.mockResolvedValue({
    status: 'awaiting_user', plan_task_id: 'plan-1', assignments: [], synthesis: null, continuation: null,
  })
  render(<ProjectWorkflowPanel taskId="plan-1" result={{
    plan: {
      ...planResult.plan,
      needs_user_choice: true,
      user_question: '원하는 자동화 범위는 무엇인가요? ① 개인용 비공개 GitHub 자동 백업 ② 여러 기기 양방향 동기화 ③ GitHub Pages 등 웹 발행까지 포함',
    },
  }} />)

  expect(await screen.findByRole('button', { name: '1. 개인용 비공개 GitHub 자동 백업' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '2. 여러 기기 양방향 동기화' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '3. GitHub Pages 등 웹 발행까지 포함' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'PM 제안대로 진행' })).not.toBeInTheDocument()
})

it('상태 확인 실패를 표시하고 사용자가 다시 확인할 수 있다', async () => {
  const user = userEvent.setup()
  mockedGet
    .mockRejectedValueOnce(new TaskApiError('서버 연결 실패'))
    .mockResolvedValueOnce({ status: 'not_started', plan_task_id: 'plan-1', assignments: [], synthesis: null })

  render(<ProjectWorkflowPanel taskId="plan-1" result={planResult} />)

  expect(await screen.findByRole('alert')).toHaveTextContent('서버 연결 실패')
  await user.click(screen.getByRole('button', { name: '다시 확인' }))

  expect(mockedGet).toHaveBeenCalledTimes(2)
  expect(await screen.findByRole('button', { name: '계획 실행' })).toBeEnabled()
})

it('의견 충돌이 감지되면 재검토와 반박 진행 상태를 보여준다', async () => {
  mockedGet.mockResolvedValue({
    status: 'discussing', plan_task_id: 'plan-1', synthesis: null,
    assignments: [],
    discussions: [{
      id: 'discussion-1', employee_id: 'research-factcheck', task_type: 'employee_discussion',
      title: '출시 순서 재검토', status: 'running', attempt_count: 1, max_attempts: 3,
      started_at: '2026-08-28T00:00:00Z', finished_at: null, created_at: '2026-08-28T00:00:00Z',
    }],
  })

  render(<ProjectWorkflowPanel
    taskId="plan-1"
    result={planResult}
    employeeNames={new Map([['research-factcheck', '리서치·팩트체크']])}
  />)

  expect(await screen.findByText('의견 충돌을 재검토하고 반박하는 중')).toBeInTheDocument()
  expect(screen.getByText('의견 충돌 재검토')).toBeInTheDocument()
  expect(screen.getAllByText('리서치·팩트체크')).toHaveLength(2)
  expect(screen.getByText('진행 중')).toBeInTheDocument()
})
