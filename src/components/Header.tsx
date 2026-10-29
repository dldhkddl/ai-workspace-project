import { Bell, Search, Settings } from 'lucide-react'
import { formatConnectionSummary } from '../status'
import type { Employee } from '../types'

export function Header({ employees }: { employees: Employee[] }) {
  return (
    <header className="app-header">
      <div className="brand-lockup">
        <span className="brand-lockup__mark" aria-hidden="true">P</span>
        <div>
          <p className="app-header__eyebrow">SAMPLEBUSINESS · AI COMPANY</p>
          <h1>샘플업체 AI 운영센터</h1>
        </div>
      </div>
      <div className="app-header__right">
        <div className="app-header__summary" aria-label="전체 연결 상태">
          <span className="pulse-dot" aria-hidden="true" />
          <strong>{formatConnectionSummary(employees)}</strong>
        </div>
        <div className="header-tools" aria-label="상단 도구">
          <button type="button" disabled aria-label="검색, 준비 중"><Search aria-hidden="true" size={18} /></button>
          <button type="button" disabled aria-label="알림, 준비 중"><Bell aria-hidden="true" size={18} /></button>
          <button type="button" disabled aria-label="설정, 준비 중"><Settings aria-hidden="true" size={18} /></button>
        </div>
      </div>
    </header>
  )
}
