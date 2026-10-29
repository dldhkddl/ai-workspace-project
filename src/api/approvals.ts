import { ApiError, isRecord, isString, requestJson, stringList } from './http'

export type ApprovalStatus =
  | 'pending' | 'approved' | 'applying' | 'blocked'
  | 'applied' | 'rejected' | 'invalidated' | 'expired'

export interface ApprovalSummary {
  id: string
  project_id: string
  task_id: string
  thread_id: string
  branch: string
  status: ApprovalStatus
  revision: number
  result_sha: string
  has_sensitive_changes: boolean
  expires_at: string | null
  applied_at: string | null
  created_at: string
}

export interface ApprovalDetail extends ApprovalSummary {
  base_sha: string
  result_tree: string
  impact_hash: string
  target_branch: string | null
  target_sha_at_approval: string | null
  applied_sha: string | null
  invalidated_reason: string | null
  last_apply_error: string | null
  apply_attempt: number
  actor_id: string | null
  sensitive_files: string[]
  task_title: string | null
  employee_id: string | null
  changed_files: string[]
  secret_scan_skipped_files: string[]
  diff_stat: string | null
  patch_preview: string | null
  patch_preview_truncated: boolean
}

export class ApprovalApiError extends ApiError {}

const approvalStatuses = new Set<ApprovalStatus>([
  'pending', 'approved', 'applying', 'blocked',
  'applied', 'rejected', 'invalidated', 'expired',
])

/** 아직 결론이 나지 않아 계속 지켜봐야 하는 상태. */
const liveStatuses = new Set<ApprovalStatus>(['approved', 'applying'])

export const isApprovalInProgress = (status: ApprovalStatus): boolean => liveStatuses.has(status)

const messages = {
  connectionFailed: '승인 서버에 연결할 수 없습니다.',
  unreadable: '승인 서버 응답을 확인할 수 없습니다.',
  requestFailed: '승인 요청을 처리하지 못했습니다.',
}

const create = (message: string, code: string | null, status: number | null) =>
  new ApprovalApiError(message, code, status)

const request = (path: string, init?: RequestInit) =>
  requestJson(path, init, messages, create)

const isSummary = (value: unknown): value is ApprovalSummary => isRecord(value)
  && isString(value.id)
  && isString(value.status)
  && approvalStatuses.has(value.status as ApprovalStatus)
  && isString(value.result_sha)
  && Number.isSafeInteger(value.revision)
  && typeof value.has_sensitive_changes === 'boolean'

const invalid = () => new ApprovalApiError('승인 응답 형식이 올바르지 않습니다.')

function toDetail(payload: unknown): ApprovalDetail {
  if (!isSummary(payload)) throw invalid()
  const summaryPart: ApprovalSummary = payload
  const value = payload as unknown as Record<string, unknown>
  return {
    ...summaryPart,
    base_sha: isString(value.base_sha) ? value.base_sha : '',
    result_tree: isString(value.result_tree) ? value.result_tree : '',
    impact_hash: isString(value.impact_hash) ? value.impact_hash : '',
    target_branch: isString(value.target_branch) ? value.target_branch : null,
    target_sha_at_approval: isString(value.target_sha_at_approval) ? value.target_sha_at_approval : null,
    applied_sha: isString(value.applied_sha) ? value.applied_sha : null,
    invalidated_reason: isString(value.invalidated_reason) ? value.invalidated_reason : null,
    last_apply_error: isString(value.last_apply_error) ? value.last_apply_error : null,
    apply_attempt: Number.isSafeInteger(value.apply_attempt) ? (value.apply_attempt as number) : 0,
    actor_id: isString(value.actor_id) ? value.actor_id : null,
    sensitive_files: stringList(value.sensitive_files),
    task_title: isString(value.task_title) ? value.task_title : null,
    employee_id: isString(value.employee_id) ? value.employee_id : null,
    changed_files: stringList(value.changed_files),
    secret_scan_skipped_files: stringList(value.secret_scan_skipped_files),
    diff_stat: isString(value.diff_stat) ? value.diff_stat : null,
    patch_preview: isString(value.patch_preview) ? value.patch_preview : null,
    patch_preview_truncated: value.patch_preview_truncated === true,
  }
}

export interface ApprovalListQuery {
  status?: ApprovalStatus | null
  limit?: number
  offset?: number
}

export async function listApprovals(query: ApprovalListQuery = {}): Promise<ApprovalSummary[]> {
  const params = new URLSearchParams()
  if (query.status) params.set('status', query.status)
  params.set('limit', String(query.limit ?? 50))
  params.set('offset', String(query.offset ?? 0))

  const payload = await request(`/api/v1/approvals?${params.toString()}`)
  if (!Array.isArray(payload) || !payload.every(isSummary)) throw invalid()
  return payload as ApprovalSummary[]
}

export async function getApproval(approvalId: string): Promise<ApprovalDetail> {
  return toDetail(await request(`/api/v1/approvals/${encodeURIComponent(approvalId)}`))
}

export type ApprovalAction = 'approve' | 'reject' | 'retry'

export async function decideApproval(
  approvalId: string,
  action: ApprovalAction,
  body: { result_sha: string; revision: number; acknowledge_sensitive?: boolean },
): Promise<ApprovalDetail> {
  // 대상 브랜치와 HEAD 는 서버가 Git 에서 직접 읽는다. 여기서 보내지 않는다.
  return toDetail(await request(
    `/api/v1/approvals/${encodeURIComponent(approvalId)}/${action}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  ))
}
