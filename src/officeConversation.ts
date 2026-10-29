import type { Activity } from './types'

export interface MeetingSpeech {
  activityId: string
  employeeId: string
  message: string
}

const textValue = (value: unknown): string | null => typeof value === 'string' && value.trim()
  ? value.trim()
  : null

const cleanTaskTitle = (title: string): string => title
  .replace(/^\[PM 배분\]\s*/, '')
  .replace(/^\[PM 종합\]\s*/, '')
  .replace(/^\[PM 의견 비교\]\s*/, '')
  .replace(/^\[PM 최종 종합\]\s*/, '')
  .replace(/^\[PM 토론\]\s*/, '')

export function meetingSpeechFromActivity(activity: Activity): MeetingSpeech | null {
  const employeeId = activity.employeeId
  const taskTitle = textValue(activity.detail.task_title)
  const taskType = textValue(activity.detail.task_type)
  const parentTaskId = textValue(activity.detail.parent_task_id)
  const meetingMessage = textValue(activity.detail.meeting_message)
  if (!employeeId || !taskTitle || !taskType) return null

  const title = cleanTaskTitle(taskTitle)
  if (taskType === 'project_plan') {
    const messages: Record<string, string> = {
      'task.queued': `요청을 확인하고 계획을 세우겠습니다: ${title}`,
      'task.started': `담당 직원을 정하고 있습니다: ${title}`,
      'task.completed': `실행 계획을 정리했습니다: ${title}`,
      'task.failed': `${title} 계획을 정리하는 중 문제가 발생했습니다.`,
    }
    const message = activity.eventType === 'task.completed' && meetingMessage
      ? meetingMessage
      : messages[activity.eventType]
    return message ? { activityId: activity.id, employeeId, message } : null
  }

  if (taskType === 'project_synthesis') {
    if (activity.eventType === 'task.queued' || activity.eventType === 'task.started') {
      return {
        activityId: activity.id,
        employeeId,
        message: `직원 의견을 모아 다음 내용을 정리하겠습니다: ${title}`,
      }
    }
    if (activity.eventType === 'task.completed') {
      return {
        activityId: activity.id,
        employeeId,
        message: meetingMessage ?? `${title} 종합을 마쳤습니다.`,
      }
    }
    return null
  }

  if (!['employee_task', 'employee_discussion'].includes(taskType) || !parentTaskId) return null
  if (taskType === 'employee_discussion') {
    const messages: Record<string, string> = {
      'task.queued': `의견 충돌 재검토를 요청받았습니다: ${title}`,
      'task.started': `상대 의견과 근거를 검토하고 있습니다: ${title}`,
      'task.completed': `${title}에 대한 재검토 의견을 전달했습니다.`,
      'task.failed': `${title} 재검토 중 문제가 발생했습니다.`,
    }
    const message = activity.eventType === 'task.completed' && meetingMessage
      ? meetingMessage
      : messages[activity.eventType]
    return message ? { activityId: activity.id, employeeId, message } : null
  }
  const messages: Record<string, string> = {
    'task.queued': `${title} 업무를 맡았습니다.`,
    'task.started': `지금 진행하겠습니다: ${title}`,
    'task.completed': `${title} 결과를 전달했습니다.`,
    'task.failed': `${title} 진행 중 문제가 발생했습니다.`,
  }
  const message = activity.eventType === 'task.completed' && meetingMessage
    ? meetingMessage
    : messages[activity.eventType]
  return message ? { activityId: activity.id, employeeId, message } : null
}

export function latestMeetingSpeech(activities: Activity[], now = Date.now()): MeetingSpeech | null {
  const recentLimit = 30_000
  for (const activity of activities) {
    const createdAt = new Date(activity.createdAt).getTime()
    if (!Number.isFinite(createdAt) || now - createdAt > recentLimit) continue
    const speech = meetingSpeechFromActivity(activity)
    if (speech) return speech
  }
  return null
}
