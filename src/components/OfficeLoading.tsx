import { LoaderCircle } from 'lucide-react'

export function OfficeLoading() {
  return (
    <main className="empty-page" id="main-content">
      <section className="empty-page__card connection-state" role="status" aria-live="polite">
        <span className="empty-page__icon connection-state__spinner" aria-hidden="true"><LoaderCircle /></span>
        <h2>운영센터 연결 중</h2>
        <span>실제 직원 상태를 불러오고 있습니다.</span>
      </section>
    </main>
  )
}
