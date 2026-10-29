import { useCallback, useEffect, useRef, useState } from 'react'
import {
  activityFromEvent, alertFromEvent, fetchActivities, fetchAlerts, fetchEvents, fetchLatestEventId, parseEvent,
} from '../api/operations'
import type { Activity, Alert, RealtimeEvent } from '../types'

export type OperationsConnectionStatus = 'connecting' | 'live' | 'retrying' | 'error'

const prependUnique = <T extends { id: string }>(items: T[], item: T): T[] =>
  [item, ...items.filter((existing) => existing.id !== item.id)].slice(0, 50)

export function useOperationsFeed() {
  const [activities, setActivities] = useState<Activity[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [connectionStatus, setConnectionStatus] = useState<OperationsConnectionStatus>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const lastEventId = useRef(Number(window.localStorage.getItem('operations.lastEventId')) || 0)
  const retryTimer = useRef<number | null>(null)
  const retryCount = useRef(0)

  const retry = useCallback(() => {
    retryCount.current = 0
    setRevision((current) => current + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let websocket: WebSocket | null = null
    let stopped = false

    const applyEvent = (event: RealtimeEvent) => {
      if (event.id <= lastEventId.current) return
      lastEventId.current = event.id
      window.localStorage.setItem('operations.lastEventId', String(event.id))
      const activity = activityFromEvent(event)
      const alert = alertFromEvent(event)
      if (activity) setActivities((current) => prependUnique(current, activity))
      if (alert) setAlerts((current) => prependUnique(current, alert))
    }

    const connect = () => {
      if (stopped) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      websocket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws/events?after_id=${lastEventId.current}`)
      websocket.onopen = () => {
        retryCount.current = 0
        setConnectionStatus('live')
        setError(null)
      }
      websocket.onmessage = (message) => {
        try {
          applyEvent(parseEvent(JSON.parse(message.data)))
        } catch {
          setError('실시간 기록 응답을 처리하지 못했습니다')
          websocket?.close()
        }
      }
      websocket.onclose = () => {
        if (stopped) return
        setConnectionStatus('retrying')
        setError('실시간 연결이 끊겨 다시 연결하고 있습니다')
        const delay = Math.min(1000 * 2 ** retryCount.current, 10_000)
        retryCount.current += 1
        retryTimer.current = window.setTimeout(connect, delay)
      }
    }

    const start = async () => {
      setConnectionStatus('connecting')
      setError(null)
      try {
        const snapshotCursor = await fetchLatestEventId(controller.signal)
        const [initialActivities, initialAlerts] = await Promise.all([
          fetchActivities(controller.signal), fetchAlerts(controller.signal),
        ])
        if (stopped) return
        setActivities(initialActivities)
        setAlerts(initialAlerts)
        const storedCursor = lastEventId.current
        const replayCursor = storedCursor > 0 && storedCursor <= snapshotCursor
          ? storedCursor
          : snapshotCursor
        lastEventId.current = replayCursor
        window.localStorage.setItem('operations.lastEventId', String(replayCursor))
        let events: RealtimeEvent[]
        do {
          events = await fetchEvents(lastEventId.current, controller.signal)
          events.forEach(applyEvent)
        } while (events.length === 500 && !stopped)
        connect()
      } catch (caught) {
        if (stopped || (caught instanceof DOMException && caught.name === 'AbortError')) return
        setConnectionStatus('error')
        setError(caught instanceof Error ? caught.message : '운영 기록을 불러오지 못했습니다')
      }
    }

    void start()
    return () => {
      stopped = true
      controller.abort()
      websocket?.close()
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current)
    }
  }, [revision])

  return { activities, alerts, connectionStatus, error, retry }
}
