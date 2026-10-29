import { describe, expect, it } from 'vitest'
import { meetingSpeechFromActivity } from './officeConversation'
import type { Activity } from './types'

const activity = (overrides: Partial<Activity>): Activity => ({
  id: 'activity-1',
  employeeId: 'research-factcheck',
  taskId: 'task-1',
  eventType: 'task.started',
  title: '작업 실행을 시작했습니다',
  detail: {
    task_title: '[PM 배분] 복숭아 판매 자료 조사',
    task_type: 'employee_task',
    parent_task_id: 'plan-1',
  },
  correlationId: 'meeting-1',
  createdAt: '2026-08-28T00:00:00Z',
  ...overrides,
})

describe('사무실 회의 말풍선', () => {
  it.each([
    ['task.queued', '요청을 확인하고 계획을 세우겠습니다: 회의내용 표시'],
    ['task.started', '담당 직원을 정하고 있습니다: 회의내용 표시'],
    ['task.completed', '실행 계획을 정리했습니다: 회의내용 표시'],
  ])('PM 계획 단계 %s도 PM 자리의 실제 발언으로 바꾼다', (eventType, message) => {
    expect(meetingSpeechFromActivity(activity({
      employeeId: 'project-manager',
      eventType,
      detail: {
        task_title: '회의내용 표시',
        task_type: 'project_plan',
        parent_task_id: null,
      },
    }))).toEqual({
      employeeId: 'project-manager',
      message,
      activityId: 'activity-1',
    })
  })

  it('PM이 배분한 직원 작업 시작을 실제 직원 발언으로 바꾼다', () => {
    expect(meetingSpeechFromActivity(activity({}))).toEqual({
      employeeId: 'research-factcheck',
      message: '지금 진행하겠습니다: 복숭아 판매 자료 조사',
      activityId: 'activity-1',
    })
  })

  it('PM 계획에 질문이 남으면 완료 대신 실제 질문을 말한다', () => {
    expect(meetingSpeechFromActivity(activity({
      employeeId: 'project-manager',
      eventType: 'task.completed',
      detail: {
        task_title: '옵시디언',
        task_type: 'project_plan',
        parent_task_id: null,
        meeting_message: '확인이 필요합니다: 어떤 옵시디언 작업을 원하시나요?',
      },
    }))).toEqual({
      employeeId: 'project-manager',
      message: '확인이 필요합니다: 어떤 옵시디언 작업을 원하시나요?',
      activityId: 'activity-1',
    })
  })

  it('직원 작업 완료 시 상태 문구가 아니라 실제 결과 요약을 말한다', () => {
    expect(meetingSpeechFromActivity(activity({
      eventType: 'task.completed',
      detail: {
        task_title: '[PM 배분] 복숭아 판매 자료 조사',
        task_type: 'employee_task',
        parent_task_id: 'plan-1',
        meeting_message: '20대 고객은 짧은 영상에서 구매 전환율이 높았습니다.',
      },
    }))).toEqual({
      employeeId: 'research-factcheck',
      message: '20대 고객은 짧은 영상에서 구매 전환율이 높았습니다.',
      activityId: 'activity-1',
    })
  })

  it('직원 결과 종합 중에는 PM 발언으로 표시한다', () => {
    expect(meetingSpeechFromActivity(activity({
      employeeId: 'project-manager',
      detail: {
        task_title: '[PM 종합] 복숭아 판매 개선안',
        task_type: 'project_synthesis',
        parent_task_id: 'plan-1',
      },
    }))).toEqual({
      employeeId: 'project-manager',
      message: '직원 의견을 모아 다음 내용을 정리하겠습니다: 복숭아 판매 개선안',
      activityId: 'activity-1',
    })
  })

  it('PM 회의와 무관한 일반 작업은 말풍선으로 만들지 않는다', () => {
    expect(meetingSpeechFromActivity(activity({
      detail: { task_title: '개별 조사', task_type: 'employee_task', parent_task_id: null },
    }))).toBeNull()
  })
})
