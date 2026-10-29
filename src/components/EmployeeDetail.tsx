import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { CircleOff, LockKeyhole, PlugZap, X } from 'lucide-react'
import { createEmployeeTask, TaskApiError } from '../api/tasks'
import type { Employee, Team } from '../types'
import { StatusBadge } from './StatusBadge'

interface EmployeeDetailProps {
  employee: Employee
  team: Team
  employeeNames?: ReadonlyMap<string, string>
  onClose: () => void
}

const localEmployeeIds = new Set([
  'general-manager', 'project-manager', 'research-factcheck', 'data-analyst', 'tech-lead',
  'frontend', 'backend-integration', 'ai-automation', 'quality-assurance', 'devops-security',
  'ui-ux-designer', 'marketing-lead', 'content-copy', 'performance-analyst',
  'customer-inquiry', 'order-delivery', 'returns-voc', 'risk-analyst',
])

export function EmployeeDetail({ employee, team, onClose }: EmployeeDetailProps) {
  const panelRef = useRef<HTMLElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const connectedSource = employee.connection.status === 'not_connected'
    ? null
    : employee.connection.source
  const [showTaskForm, setShowTaskForm] = useState(false)
  const [title, setTitle] = useState('')
  const [request, setRequest] = useState('')
  const [taskError, setTaskError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const canRequestTask = localEmployeeIds.has(employee.id)
    && !['not_connected', 'setup_required', 'error', 'stopped'].includes(employee.connection.status)

  const submitTask = async (event: FormEvent) => {
    event.preventDefault()
    if (!title.trim() || !request.trim() || submitting) return
    setSubmitting(true)
    setTaskError(null)
    try {
      await createEmployeeTask(
        employee.id, title.trim(), request.trim(), crypto.randomUUID(),
      )
      onClose()
    } catch (error) {
      setTaskError(error instanceof TaskApiError ? error.message : '작업 요청 중 오류가 발생했습니다.')
    } finally {
      setSubmitting(false)
    }
  }

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeButtonRef.current?.focus()
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [])

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }

    if (event.key !== 'Tab') return
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], [tabindex]:not([tabindex="-1"])')
    if (!focusable?.length) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <aside
      ref={panelRef}
      id="employee-detail"
      className="employee-detail"
      role="dialog"
      aria-modal="true"
      aria-labelledby="employee-detail-title"
      onKeyDown={handleKeyDown}
    >
      <div className="employee-detail__topline" style={{ backgroundColor: team.color }} />
      <header className="employee-detail__header">
        <div>
          <p>{team.name}</p>
          <h2 id="employee-detail-title">{employee.name}</h2>
        </div>
        <button ref={closeButtonRef} type="button" className="icon-button" aria-label="직원 상세 닫기" onClick={onClose}>
          <X aria-hidden="true" size={20} />
        </button>
      </header>

      <p className="employee-detail__role">{employee.role}</p>
      <div className="employee-detail__status-row">
        <span>현재 상태</span>
        <StatusBadge status={employee.connection.status} />
      </div>

      <dl className="detail-list">
        <div>
          <dt><PlugZap aria-hidden="true" size={16} /> {connectedSource ? '연결 대상' : '예정 연결 대상'}</dt>
          <dd>{connectedSource ?? employee.plannedSource}</dd>
        </div>
        <div>
          <dt><CircleOff aria-hidden="true" size={16} /> 사용 가능 기능</dt>
          <dd>사용 가능 기능 없음</dd>
        </div>
        <div>
          <dt><LockKeyhole aria-hidden="true" size={16} /> 다음 단계</dt>
          <dd>연결 설계 및 기능 점검</dd>
        </div>
      </dl>

      <section className="planned-features" aria-labelledby="planned-features-title">
        <h3 id="planned-features-title">연결 후 예정 기능</h3>
        <ul>
          {employee.plannedFeatures.map((feature) => <li key={feature}>{feature}</li>)}
        </ul>
      </section>

      <div className="employee-detail__actions">
        <button type="button" disabled>질문하기</button>
        <button type="button" disabled={!canRequestTask || submitting} onClick={() => setShowTaskForm(true)}>작업 요청</button>
      </div>
      {!canRequestTask ? (
        <p className="employee-detail__notice">
          {employee.connection.error?.message ?? '현재 연결 준비 중인 직원입니다.'}
        </p>
      ) : null}

      {showTaskForm ? (
        <form className="employee-task-form" onSubmit={submitTask}>
          <label htmlFor="employee-task-title">작업 제목</label>
          <input
            id="employee-task-title"
            value={title}
            maxLength={200}
            required
            onChange={(event) => setTitle(event.target.value)}
          />
          <label htmlFor="employee-task-request">요청 내용</label>
          <textarea
            id="employee-task-request"
            value={request}
            rows={5}
            required
            onChange={(event) => setRequest(event.target.value)}
          />
          <div className="employee-task-form__actions">
            <button type="button" onClick={() => setShowTaskForm(false)} disabled={submitting}>취소</button>
            <button type="submit" disabled={submitting}>{submitting ? '작업 진행 중…' : '요청 보내기'}</button>
          </div>
        </form>
      ) : null}

      {taskError ? (
        <div className="employee-task-error" role="alert">
          <p>{taskError}</p>
          <button type="button" onClick={() => setShowTaskForm(true)}>다시 시도</button>
        </div>
      ) : null}
    </aside>
  )
}
