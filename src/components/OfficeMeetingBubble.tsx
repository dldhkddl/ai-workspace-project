import { useEffect, useState } from 'react'
import type { MeetingSpeech } from '../officeConversation'

export function OfficeMeetingBubble({ speech, employeeName }: {
  speech: MeetingSpeech | null
  employeeName: string
}) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (!speech) {
      setVisible(false)
      return
    }
    setVisible(true)
    const timer = window.setTimeout(() => setVisible(false), 30_000)
    return () => window.clearTimeout(timer)
  }, [speech?.activityId])

  if (!speech || !visible) return null

  return (
    <aside className="office-meeting-bubble" role="status" aria-live="polite">
      <strong>{employeeName}</strong>
      <span>{speech.message}</span>
    </aside>
  )
}
