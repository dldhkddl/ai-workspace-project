import { useEffect, useState } from 'react'
import { CheckCheck, MessageCircleQuestion, RotateCcw } from 'lucide-react'
import type { ApprovalStatus } from '../api/approvals'
import { useApprovals, type ApprovalStatusFilter } from '../hooks/useApprovals'
import { usePmConfirmations } from '../hooks/usePmConfirmations'
import { ProjectWorkflowPanel } from './ProjectWorkflowPanel'

const statusLabels: Record<ApprovalStatus, string> = {
  pending: '승인 대기',
  approved: '적용 대기',
  applying: '적용 중',
  blocked: '막힘',
  applied: '반영됨',
  rejected: '거절됨',
  invalidated: '무효',
  expired: '만료',
}

const statusFilters: Array<{ value: ApprovalStatusFilter; label: string }> = [
  { value: 'pending', label: '승인 대기' },
  { value: 'blocked', label: '막힘' },
  { value: 'applied', label: '반영됨' },
  { value: 'all', label: '전체' },
]

const timeFormat = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', dateStyle: 'short', timeStyle: 'short',
})

const formatTime = (value: string): string => {
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : timeFormat.format(time)
}

function FileList({ title, files, note }: { title: string; files: string[]; note?: string }) {
  if (files.length === 0) return null
  return (
    <div className="approval-detail__files">
      <h4>{title}</h4>
      <ul>{files.map((file) => <li key={file}>{file}</li>)}</ul>
      {note ? <p>{note}</p> : null}
    </div>
  )
}

export function ApprovalCenter({ employeeNames, onPmResponseSubmitted }: {
  employeeNames?: ReadonlyMap<string, string>
  onPmResponseSubmitted?: (taskId: string) => void
}) {
  const approvals = useApprovals()
  const pmConfirmations = usePmConfirmations()
  const { detail, deciding } = approvals
  const [acknowledged, setAcknowledged] = useState(false)
  const [showPatch, setShowPatch] = useState(false)

  // 다른 승인을 열면 확인 상태와 펼침을 초기화한다.
  useEffect(() => {
    setAcknowledged(false)
    setShowPatch(false)
  }, [approvals.selectedId])

  const needsConfirmation = detail?.has_sensitive_changes === true
  const canApprove = detail?.status === 'pending'
    && !deciding
    && (!needsConfirmation || acknowledged)
  const showPmConfirmations = approvals.statusFilter === 'pending' || approvals.statusFilter === 'all'
  const nothingPending = approvals.listState === 'ready'
    && approvals.approvals.length === 0
    && (!showPmConfirmations || (!pmConfirmations.loading && pmConfirmations.tasks.length === 0))

  return (
    <main id="main-content" className="approval-center" tabIndex={-1}>
      <header className="approval-center__header">
        <h2><CheckCheck aria-hidden="true" size={20} /> 승인</h2>
        <div className="approval-center__filters">
          <label htmlFor="approval-status-filter">상태</label>
          <select
            id="approval-status-filter"
            value={approvals.statusFilter}
            onChange={(event) => approvals.setStatusFilter(event.target.value as ApprovalStatusFilter)}
          >
            {statusFilters.map(({ value, label }) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
      </header>

      {approvals.listState === 'loading' ? <p role="status">승인 목록을 불러오는 중입니다…</p> : null}

      {approvals.listState === 'error' ? (
        <div className="task-history__error" role="alert">
          <p>{approvals.listError}</p>
          <button type="button" onClick={() => void approvals.reload()}>
            <RotateCcw aria-hidden="true" size={15} /> 다시 시도
          </button>
        </div>
      ) : null}

      {showPmConfirmations && pmConfirmations.error ? (
        <div className="task-history__error" role="alert">
          <p>{pmConfirmations.error}</p>
          <button type="button" onClick={() => void pmConfirmations.reload()}>
            <RotateCcw aria-hidden="true" size={15} /> 다시 시도
          </button>
        </div>
      ) : null}

      {showPmConfirmations && pmConfirmations.tasks.length > 0 ? (
        <section className="approval-center__group" aria-label="PM 확인 대기">
          <h3><MessageCircleQuestion aria-hidden="true" size={18} /> PM 확인 대기 {pmConfirmations.tasks.length}건</h3>
          <ul className="task-history__list">
            {pmConfirmations.tasks.map((task) => (
              <li key={task.id}>
                <button
                  type="button"
                  className={task.id === pmConfirmations.selectedId ? 'task-row task-row--selected' : 'task-row'}
                  aria-current={task.id === pmConfirmations.selectedId ? 'true' : undefined}
                  onClick={() => {
                    approvals.select(null)
                    void pmConfirmations.select(task.id === pmConfirmations.selectedId ? null : task.id)
                  }}
                >
                  <span className="task-row__status task-row__status--pending">답변 필요</span>
                  <span className="task-row__title">{task.title}</span>
                  <span className="task-row__employee">프로젝트 매니저</span>
                  <span className="task-row__time">{formatTime(task.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {showPmConfirmations && pmConfirmations.selectedId !== null ? (
        <section className="approval-detail approval-detail--pm" aria-label="PM 확인 상세">
          {pmConfirmations.detail === null ? <p role="status">PM 확인 내용을 불러오는 중입니다…</p> : null}
          {pmConfirmations.detail?.result ? (
            <>
              <h3>{pmConfirmations.detail.title}</h3>
              <ProjectWorkflowPanel
                taskId={pmConfirmations.detail.id}
                result={pmConfirmations.detail.result}
                employeeNames={employeeNames}
                onResponseSubmitted={(taskId) => {
                  pmConfirmations.resolved()
                  onPmResponseSubmitted?.(taskId)
                }}
              />
            </>
          ) : null}
        </section>
      ) : null}

      {nothingPending ? (
        <p className="task-history__empty">조건에 맞는 승인 요청이 없습니다.</p>
      ) : null}

      {approvals.approvals.length > 0 ? (
        <section className="approval-center__group" aria-label="코드 변경 승인">
          <h3>코드 변경 승인 {approvals.approvals.length}건</h3>
          <ul className="task-history__list" aria-label="승인 목록">
            {approvals.approvals.map((approval) => (
            <li key={approval.id}>
              <button
                type="button"
                className={approval.id === approvals.selectedId ? 'task-row task-row--selected' : 'task-row'}
                aria-current={approval.id === approvals.selectedId ? 'true' : undefined}
                onClick={() => {
                  void pmConfirmations.select(null)
                  approvals.select(approval.id === approvals.selectedId ? null : approval.id)
                }}
              >
                <span className={`task-row__status task-row__status--${approval.status}`}>
                  {statusLabels[approval.status]}
                </span>
                <span className="task-row__title">{approval.branch}</span>
                <span className="task-row__employee">{approval.result_sha.slice(0, 7)}</span>
                <span className="task-row__time">{formatTime(approval.created_at)}</span>
                {approval.has_sensitive_changes ? (
                  <span className="task-row__attempts">확인 필요</span>
                ) : null}
              </button>
            </li>
            ))}
          </ul>
        </section>
      ) : null}

      {approvals.selectedId !== null ? (
        <section className="approval-detail" aria-label="승인 상세">
          {approvals.detailError ? (
            <p className="task-history__detail-error" role="alert">{approvals.detailError}</p>
          ) : null}

          {detail !== null ? (
            <>
              <h3>{detail.task_title ?? detail.branch}</h3>
              <p className="task-history__detail-status">
                {statusLabels[detail.status]}
                {detail.status === 'approved' ? ' · 적용 작업을 기다리는 중입니다' : ''}
                {detail.status === 'applying' ? ' · 원본에 반영하는 중입니다' : ''}
                {detail.status === 'applied' && detail.applied_sha
                  ? ` · ${detail.applied_sha.slice(0, 7)}` : ''}
              </p>

              {detail.status === 'blocked' ? (
                <div className="approval-detail__blocked" role="alert">
                  <p>{detail.last_apply_error ?? '원본 상태 때문에 적용이 멈췄습니다.'}</p>
                  <p>원본의 변경사항을 커밋하거나 되돌린 뒤 다시 시도하세요.</p>
                </div>
              ) : null}

              {detail.invalidated_reason ? (
                <p className="task-history__detail-error">무효 사유: {detail.invalidated_reason}</p>
              ) : null}

              {detail.diff_stat ? (
                <pre className="employee-task-result__diff">{detail.diff_stat}</pre>
              ) : null}

              <FileList title="변경 파일" files={detail.changed_files} />
              <FileList
                title="별도 확인이 필요한 파일"
                files={detail.sensitive_files}
                note="검증 명령이나 실행 경로를 바꿀 수 있는 파일입니다. 승인 전에 내용을 직접 확인하세요."
              />
              <FileList
                title="비밀정보 검사를 못 한 파일"
                files={detail.secret_scan_skipped_files}
                note="크기나 형식 때문에 내용을 확인하지 못했습니다. 검사하지 못한 것과 안전한 것은 다릅니다."
              />

              {detail.patch_preview ? (
                <div className="approval-detail__patch">
                  <button type="button" onClick={() => setShowPatch((shown) => !shown)}>
                    {showPatch ? '전체 변경 접기' : '전체 변경 보기'}
                  </button>
                  {detail.patch_preview_truncated ? (
                    <span className="approval-detail__badge">일부만 표시됨</span>
                  ) : null}
                  {showPatch ? (
                    <>
                      {detail.patch_preview_truncated ? (
                        <p className="approval-detail__note">
                          변경이 커서 요약만 표시합니다. 전체 내용은 보관된 패치 파일에 있습니다.
                        </p>
                      ) : null}
                      <pre className="approval-detail__diff">{detail.patch_preview}</pre>
                    </>
                  ) : null}
                </div>
              ) : null}

              {needsConfirmation && detail.status === 'pending' ? (
                <label className="approval-detail__acknowledge">
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(event) => setAcknowledged(event.target.checked)}
                  />
                  위 파일들을 직접 확인했으며 그대로 반영합니다
                </label>
              ) : null}

              {approvals.decisionError ? (
                <p className="task-history__detail-error" role="alert">{approvals.decisionError}</p>
              ) : null}

              <div className="approval-detail__actions">
                {detail.status === 'blocked' ? (
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => void approvals.decide('retry')}
                  >
                    {deciding ? '처리 중…' : '다시 시도'}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="approval-detail__approve"
                    disabled={!canApprove}
                    onClick={() => void approvals.decide('approve', { acknowledgeSensitive: acknowledged })}
                  >
                    {deciding ? '처리 중…' : '승인'}
                  </button>
                )}
                <button
                  type="button"
                  disabled={detail.status !== 'pending' || deciding}
                  onClick={() => void approvals.decide('reject')}
                >
                  거절
                </button>
              </div>
            </>
          ) : null}
        </section>
      ) : null}
    </main>
  )
}
