import { CircleAlert } from 'lucide-react'

interface OfficeErrorProps {
  message: string
  onRetry: () => void
}

export function OfficeError({ message, onRetry }: OfficeErrorProps) {
  return (
    <main className="empty-page" id="main-content">
      <section className="empty-page__card connection-state" role="alert">
        <span className="empty-page__icon" aria-hidden="true"><CircleAlert /></span>
        <h2>운영센터 연결 실패</h2>
        <span>{message}</span>
        <button className="connection-state__retry" type="button" onClick={onRetry}>
          다시 시도
        </button>
      </section>
    </main>
  )
}
