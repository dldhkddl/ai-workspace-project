import { BarChart3, Building2, Cable, CheckCheck, ClipboardList, FileText } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type ViewId = 'office' | 'dashboard' | 'tasks' | 'approvals' | 'reports' | 'connections'

const items: Array<{ id: ViewId; label: string; icon: LucideIcon }> = [
  { id: 'office', label: '사무실', icon: Building2 },
  { id: 'dashboard', label: '현황판', icon: BarChart3 },
  { id: 'tasks', label: '작업', icon: ClipboardList },
  { id: 'approvals', label: '승인', icon: CheckCheck },
  { id: 'reports', label: '보고서', icon: FileText },
  { id: 'connections', label: '연결 관리', icon: Cable },
]

interface NavigationProps {
  activeView: ViewId
  onChange: (view: ViewId) => void
}

export function Navigation({ activeView, onChange }: NavigationProps) {
  return (
    <nav className="primary-nav" aria-label="운영센터 주요 메뉴">
      {items.map(({ id, label, icon: Icon }) => (
        <button
          type="button"
          key={id}
          className={activeView === id ? 'primary-nav__item primary-nav__item--active' : 'primary-nav__item'}
          aria-current={activeView === id ? 'page' : undefined}
          onClick={() => onChange(id)}
        >
          <Icon aria-hidden="true" size={17} />
          {label}
        </button>
      ))}
    </nav>
  )
}
