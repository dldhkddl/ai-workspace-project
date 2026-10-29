import { BellRing, CheckCircle2, Radio } from 'lucide-react'
import type { OperationsConnectionStatus } from '../hooks/useOperationsFeed'
import type { Activity, Alert } from '../types'

interface ActivityPanelProps {
  activities: Activity[]
  alerts: Alert[]
  connectionStatus: OperationsConnectionStatus
  error: string | null
  retry: () => void
}

export function ActivityPanel({ activities, alerts, connectionStatus, error, retry }: ActivityPanelProps) {
  const connectionLabel = connectionStatus === 'live'
    ? '실시간 연결됨'
    : connectionStatus === 'retrying'
      ? '연결 끊김 · 재시도 중'
      : connectionStatus === 'connecting'
        ? '실시간 연결 중'
        : '연결 오류'

  return (
    <section className="activity-panel" aria-label="운영 활동 영역">
      <div className="activity-panel__column">
        <header><Radio aria-hidden="true" size={16} /><h2>실시간 활동 기록</h2></header>
        <span className={`activity-panel__connection activity-panel__connection--${connectionStatus}`}>{connectionLabel}</span>
        {activities.length ? (
          <ul className="activity-panel__list">
            {activities.slice(0, 4).map((activity) => (
              <li key={activity.id}>
                <strong>{activity.title}</strong>
                <time dateTime={activity.createdAt}>{new Date(activity.createdAt).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}</time>
              </li>
            ))}
          </ul>
        ) : <p>아직 연결된 활동이 없습니다</p>}
      </div>
      <div className="activity-panel__column">
        <header><BellRing aria-hidden="true" size={16} /><h2>시스템 알림</h2></header>
        {alerts.length ? (
          <ul className="activity-panel__list">
            {alerts.slice(0, 4).map((alert) => (
              <li key={alert.id} className={`activity-panel__alert activity-panel__alert--${alert.severity}`}>
                <strong>{alert.message}</strong>
                <span>{alert.severity === 'error' ? '오류' : alert.severity === 'warning' ? '주의' : '안내'}</span>
              </li>
            ))}
          </ul>
        ) : <p>새로운 시스템 알림이 없습니다</p>}
        {error ? (
          <div className="activity-panel__error" role="status">
            <span>{error}</span>
            {connectionStatus === 'error' || connectionStatus === 'retrying'
              ? <button type="button" onClick={retry}>다시 시도</button>
              : null}
          </div>
        ) : null}
      </div>
      <div className="activity-panel__column">
        <header><CheckCircle2 aria-hidden="true" size={16} /><h2>승인 대기</h2></header>
        <p>승인을 기다리는 작업이 없습니다</p>
      </div>
    </section>
  )
}
