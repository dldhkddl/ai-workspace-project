import { render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { Activity } from '../types'
import { MeetingSummaryPanel } from './MeetingSummaryPanel'

const activities: Activity[] = [
  {
    id: 'a3', employeeId: 'research-factcheck', taskId: 'worker-1', eventType: 'task.completed',
    title: '작업을 완료했습니다', correlationId: 'meeting-1', createdAt: '2026-08-28T01:03:00Z',
    detail: {
      task_title: '[PM 배분] 시장 조사', task_type: 'employee_task', parent_task_id: 'pm-1',
      meeting_message: '시장 자료를 확인했고 국내 판매부터 시작하는 것이 좋습니다.',
    },
  },
  {
    id: 'a2', employeeId: 'project-manager', taskId: 'pm-1', eventType: 'task.completed',
    title: '작업을 완료했습니다', correlationId: 'meeting-1', createdAt: '2026-08-28T01:02:00Z',
    detail: {
      task_title: '복숭아 판매 개선', task_type: 'project_plan', parent_task_id: null,
      meeting_message: '판매 채널을 먼저 선택해 주세요.',
    },
  },
  {
    id: 'other', employeeId: 'project-manager', taskId: 'pm-2', eventType: 'task.completed',
    title: '작업을 완료했습니다', correlationId: 'meeting-2', createdAt: '2026-08-28T01:04:00Z',
    detail: { task_title: '다른 회의', task_type: 'project_plan', parent_task_id: null },
  },
]

it('선택한 PM 작업의 회의 발언을 시간순으로 한곳에 정리한다', () => {
  render(<MeetingSummaryPanel
    activities={activities}
    focusedTaskId="pm-1"
    employeeNames={new Map([
      ['project-manager', '프로젝트 매니저'],
      ['research-factcheck', '리서치·팩트체크'],
    ])}
  />)

  expect(screen.getByRole('heading', { name: '회의 내용 정리' })).toBeInTheDocument()
  expect(screen.getByText('복숭아 판매 개선')).toBeInTheDocument()
  const log = screen.getByRole('list', { name: '회의 대화 내역' })
  const items = within(log).getAllByRole('listitem')
  expect(items).toHaveLength(2)
  expect(items[0]).toHaveTextContent('프로젝트 매니저')
  expect(items[0]).toHaveTextContent('판매 채널을 먼저 선택해 주세요.')
  expect(items[1]).toHaveTextContent('리서치·팩트체크')
  expect(items[1]).toHaveTextContent('국내 판매부터 시작하는 것이 좋습니다.')
  expect(screen.queryByText('다른 회의 계획을 정리했습니다')).not.toBeInTheDocument()
})

it('회의 기록이 없으면 앞으로 표시될 위치임을 안내한다', () => {
  render(<MeetingSummaryPanel activities={[]} focusedTaskId="pm-1" employeeNames={new Map()} />)

  expect(screen.getByText('회의가 시작되면 PM과 직원들의 대화가 여기에 모입니다.')).toBeInTheDocument()
})

it('직원의 재검토 반박도 회의 대화로 표시한다', () => {
  render(<MeetingSummaryPanel
    focusedTaskId="pm-1"
    employeeNames={new Map([['frontend', '프론트엔드']])}
    activities={[{
      id: 'discussion-complete', employeeId: 'frontend', taskId: 'discussion-1',
      eventType: 'task.completed', title: '작업을 완료했습니다', correlationId: 'meeting-1',
      createdAt: '2026-08-28T01:05:00Z',
      detail: {
        task_title: '[PM 토론] 출시 순서', task_type: 'employee_discussion', parent_task_id: 'pm-1',
        meeting_message: '상대 근거는 타당하지만 일정 위험 때문에 국내 우선으로 수정 제안합니다.',
      },
    }]}
  />)

  expect(screen.getByText('프론트엔드')).toBeInTheDocument()
  expect(screen.getByText(/상대 근거는 타당하지만/)).toBeInTheDocument()
})
