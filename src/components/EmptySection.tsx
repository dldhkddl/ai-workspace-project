import { BarChart3, Cable, FileText, type LucideIcon } from 'lucide-react'
import type { ViewId } from './Navigation'

type EmptyViewId = Exclude<ViewId, 'office' | 'tasks' | 'approvals'>

const content: Record<EmptyViewId, { title: string; description: string; icon: LucideIcon }> = {
  dashboard: { title: '현황판', description: '아직 연결된 지표가 없습니다', icon: BarChart3 },
  reports: { title: '보고서', description: '생성된 운영 보고서가 없습니다', icon: FileText },
  connections: { title: '연결 관리', description: '아직 연결된 프로그램이 없습니다', icon: Cable },
}

export function EmptySection({ view }: { view: EmptyViewId }) {
  const { title, description, icon: Icon } = content[view]
  return (
    <main id="main-content" className="empty-page" tabIndex={-1}>
      <div className="empty-page__card">
        <span className="empty-page__icon"><Icon aria-hidden="true" size={30} /></span>
        <p>연동 준비 중</p>
        <h2>{title}</h2>
        <span>{description}</span>
        <small>실제 프로그램 연결과 기능 시험을 통과한 데이터만 이곳에 표시됩니다.</small>
      </div>
    </main>
  )
}
