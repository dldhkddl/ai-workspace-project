import { MessageSquareText, Users } from 'lucide-react'
import { useMemo } from 'react'
import { meetingSpeechFromActivity } from '../officeConversation'
import type { Activity } from '../types'

interface MeetingEntry {
  id: string
  employeeId: string
  message: string
  createdAt: string
}

const textValue = (value: unknown): string | null => typeof value === 'string' && value.trim()
  ? value.trim()
  : null

const cleanTitle = (title: string): string => title
  .replace(/^\[PM (?:배분|종합|후속)\]\s*/, '')

function selectMeeting(activities: Activity[], focusedTaskId: string | null) {
  const speeches = activities.flatMap((activity) => {
    const speech = meetingSpeechFromActivity(activity)
    return speech ? [{ activity, speech }] : []
  })
  const focusedActivity = focusedTaskId
    ? activities.find((activity) => activity.taskId === focusedTaskId)
      ?? activities.find((activity) => textValue(activity.detail.parent_task_id) === focusedTaskId)
    : null
  const correlationId = focusedActivity?.correlationId ?? speeches[0]?.activity.correlationId ?? null
  const scoped = correlationId
    ? speeches.filter(({ activity }) => activity.correlationId === correlationId)
    : []
  const planActivity = scoped.find(({ activity }) => activity.detail.task_type === 'project_plan')?.activity
  const title = textValue(planActivity?.detail.task_title) ?? '진행 중인 회의'
  const entries: MeetingEntry[] = scoped
    .map(({ activity, speech }) => ({
      id: activity.id,
      employeeId: speech.employeeId,
      message: speech.message,
      createdAt: activity.createdAt,
    }))
    .sort((left, right) => new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime())
  return { title: cleanTitle(title), entries }
}

export function MeetingSummaryPanel({ activities, focusedTaskId, employeeNames }: {
  activities: Activity[]
  focusedTaskId: string | null
  employeeNames: ReadonlyMap<string, string>
}) {
  const meeting = useMemo(
    () => selectMeeting(activities, focusedTaskId),
    [activities, focusedTaskId],
  )
  const participants = Array.from(new Set(meeting.entries.map((entry) => entry.employeeId)))
  const formatTime = (value: string) => new Date(value).toLocaleTimeString('ko-KR', {
    hour: '2-digit', minute: '2-digit',
  })

  return (
    <aside className="meeting-summary" aria-label="회의 내용 정리">
      <header className="meeting-summary__header">
        <div>
          <span className="meeting-summary__eyebrow"><MessageSquareText aria-hidden="true" size={15} /> 회의 기록</span>
          <h2>회의 내용 정리</h2>
        </div>
        {meeting.entries.length > 0 ? <span className="meeting-summary__count">{meeting.entries.length}개 발언</span> : null}
      </header>

      {meeting.entries.length === 0 ? (
        <p className="meeting-summary__empty">회의가 시작되면 PM과 직원들의 대화가 여기에 모입니다.</p>
      ) : (
        <>
          <section className="meeting-summary__overview">
            <strong>{meeting.title}</strong>
            <span><Users aria-hidden="true" size={13} /> 참여 {participants.length}명</span>
          </section>
          <ol className="meeting-summary__log" aria-label="회의 대화 내역">
            {meeting.entries.map((entry) => (
              <li key={entry.id}>
                <div className="meeting-summary__speaker">
                  <strong>{employeeNames.get(entry.employeeId) ?? entry.employeeId}</strong>
                  <time dateTime={entry.createdAt}>{formatTime(entry.createdAt)}</time>
                </div>
                <p>{entry.message}</p>
              </li>
            ))}
          </ol>
        </>
      )}
    </aside>
  )
}
