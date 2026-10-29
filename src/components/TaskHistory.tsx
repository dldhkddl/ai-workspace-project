import { useEffect, useRef } from 'react'
import { ClipboardList, RotateCcw } from 'lucide-react'
import { isTerminalTaskStatus, type TaskStatus } from '../api/tasks'
import { useTaskHistory, type TaskStatusFilter } from '../hooks/useTaskHistory'
import type { Employee } from '../types'
import { TaskResultView } from './TaskResultView'
import { ProjectWorkflowPanel } from './ProjectWorkflowPanel'

const statusLabels: Record<TaskStatus, string> = {
  queued: '대기',
  running: '진행 중',
  cancel_requested: '취소 요청',
  cancelled: '취소됨',
  completed: '완료',
  failed: '실패',
}

const statusFilters: Array<{ value: TaskStatusFilter; label: string }> = [
  { value: 'all', label: '전체' },
  { value: 'queued', label: '대기' },
  { value: 'running', label: '진행 중' },
  { value: 'completed', label: '완료' },
  { value: 'failed', label: '실패' },
  { value: 'cancelled', label: '취소됨' },
]

const timeFormat = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  dateStyle: 'short',
  timeStyle: 'short',
})

const formatTime = (value: string): string => {
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : timeFormat.format(time)
}

const taskStatusLabel = (task: { status: TaskStatus; requires_user_response?: boolean }) =>
  task.requires_user_response ? '답변 필요' : statusLabels[task.status]

export function TaskHistory({ employees }: { employees: Employee[] }) {
  const history = useTaskHistory()
  const employeeNames = new Map(employees.map((employee) => [employee.id, employee.name]))
  const { detail, detailState, detailError, selectedId } = history
  const detailRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (selectedId && detailState === 'ready') {
      const detailElement = detailRef.current
      if (detailElement && typeof detailElement.scrollIntoView === 'function') {
        detailElement.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }
  }, [detailState, selectedId])

  return (
    <main id="main-content" className="task-history" tabIndex={-1}>
      <header className="task-history__header">
        <h2><ClipboardList aria-hidden="true" size={20} /> 작업 기록</h2>
        <div className="task-history__filters">
          <label htmlFor="task-status-filter">상태</label>
          <select
            id="task-status-filter"
            value={history.statusFilter}
            onChange={(event) => history.changeStatusFilter(event.target.value as TaskStatusFilter)}
          >
            {statusFilters.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>

          <label htmlFor="task-employee-filter">직원</label>
          <select
            id="task-employee-filter"
            value={history.employeeFilter}
            onChange={(event) => history.changeEmployeeFilter(event.target.value)}
          >
            <option value="all">전체</option>
            {employees.map((employee) => (
              <option key={employee.id} value={employee.id}>{employee.name}</option>
            ))}
          </select>
        </div>
      </header>

      {history.listState === 'loading' ? <p role="status">작업 기록을 불러오는 중입니다…</p> : null}

      {history.listState === 'error' ? (
        <div className="task-history__error" role="alert">
          <p>{history.listError}</p>
          <button type="button" onClick={() => void history.reload()}>
            <RotateCcw aria-hidden="true" size={15} /> 다시 시도
          </button>
        </div>
      ) : null}

      {history.listState === 'ready' && history.tasks.length === 0 ? (
        <p className="task-history__empty">조건에 맞는 작업이 없습니다.</p>
      ) : null}

      {history.tasks.length > 0 ? (
        <ul className="task-history__list" aria-label="작업 목록">
          {history.tasks.map((task) => (
            <li key={task.id}>
              <button
                type="button"
                className={task.id === selectedId ? 'task-row task-row--selected' : 'task-row'}
                aria-current={task.id === selectedId ? 'true' : undefined}
                onClick={() => history.select(task.id === selectedId ? null : task.id)}
              >
                <span className={`task-row__status task-row__status--${task.status}`}>
                  {taskStatusLabel(task)}
                </span>
                <span className="task-row__title">{task.title}</span>
                <span className="task-row__employee">{employeeNames.get(task.employee_id) ?? task.employee_id}</span>
                <span className="task-row__time">{formatTime(task.created_at)}</span>
                {task.attempt_count > 1 ? (
                  <span className="task-row__attempts">재시도 {task.attempt_count - 1}회</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {history.hasMore ? (
        <button
          type="button"
          className="task-history__more"
          disabled={history.loadingMore}
          onClick={() => void history.loadMore()}
        >
          {history.loadingMore ? '불러오는 중…' : '더 보기'}
        </button>
      ) : null}

      {selectedId !== null ? (
        <section ref={detailRef} className="task-history__detail" aria-label="작업 상세">
          {detailState === 'loading' && detail === null ? <p role="status">작업 결과를 불러오는 중입니다…</p> : null}

          {detailState === 'error' ? (
            <div className="task-history__error" role="alert">
              <p>{detailError}</p>
              <button type="button" onClick={history.retryDetail}>
                <RotateCcw aria-hidden="true" size={15} /> 다시 시도
              </button>
            </div>
          ) : null}

          {detail !== null ? (
            <>
              <h3>{detail.title}</h3>
              <p className="task-history__detail-status">
                {taskStatusLabel(detail)}
                {!isTerminalTaskStatus(detail.status) && detail.will_retry ? ' · 자동 재시도 대기 중' : ''}
              </p>
              {detail.error !== null ? (
                <p className="task-history__detail-error" role="alert">
                  실패 사유: {detail.error.message} ({detail.error.code})
                </p>
              ) : null}
              <TaskResultView result={detail.result} />
              {detail.task_type === 'project_plan' && detail.result ? (
                <ProjectWorkflowPanel taskId={detail.id} result={detail.result} employeeNames={employeeNames} />
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}
    </main>
  )
}
