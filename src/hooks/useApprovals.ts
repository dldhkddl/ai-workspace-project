import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApprovalApiError,
  decideApproval,
  getApproval,
  isApprovalInProgress,
  listApprovals,
  type ApprovalAction,
  type ApprovalDetail,
  type ApprovalStatus,
  type ApprovalSummary,
} from '../api/approvals'

const REFRESH_INTERVAL = 3000

export type ApprovalListState = 'loading' | 'ready' | 'error'
export type ApprovalStatusFilter = ApprovalStatus | 'all'

const message = (error: unknown, fallback: string) =>
  error instanceof ApprovalApiError ? error.message : fallback

export function useApprovals() {
  const [statusFilter, setStatusFilter] = useState<ApprovalStatusFilter>('pending')
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([])
  const [listState, setListState] = useState<ApprovalListState>('loading')
  const [listError, setListError] = useState<string | null>(null)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ApprovalDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [deciding, setDeciding] = useState(false)
  const [decisionError, setDecisionError] = useState<string | null>(null)

  const listRequestId = useRef(0)
  const detailRequestId = useRef(0)

  const fetchList = useCallback(
    () => listApprovals({ status: statusFilter === 'all' ? null : statusFilter }),
    [statusFilter],
  )

  const reload = useCallback(async () => {
    const requestId = (listRequestId.current += 1)
    setListState('loading')
    setListError(null)
    try {
      const page = await fetchList()
      if (requestId !== listRequestId.current) return
      setApprovals(page)
      setListState('ready')
    } catch (error) {
      if (requestId !== listRequestId.current) return
      setListError(message(error, '승인 목록을 불러오지 못했습니다.'))
      setListState('error')
    }
  }, [fetchList])

  useEffect(() => {
    void reload()
  }, [reload])

  const loadDetail = useCallback(async (approvalId: string) => {
    const requestId = (detailRequestId.current += 1)
    setDetailError(null)
    try {
      const loaded = await getApproval(approvalId)
      if (requestId !== detailRequestId.current) return
      setDetail(loaded)
    } catch (error) {
      if (requestId !== detailRequestId.current) return
      setDetailError(message(error, '승인 내용을 불러오지 못했습니다.'))
    }
  }, [])

  const select = useCallback((approvalId: string | null) => {
    setSelectedId(approvalId)
    setDecisionError(null)
    detailRequestId.current += 1
    setDetail(null)
    setDetailError(null)
    if (approvalId !== null) void loadDetail(approvalId)
  }, [loadDetail])

  const decide = useCallback(async (
    action: ApprovalAction,
    options: { acknowledgeSensitive?: boolean } = {},
  ) => {
    if (detail === null || deciding) return
    setDeciding(true)
    setDecisionError(null)
    try {
      const updated = await decideApproval(detail.id, action, {
        result_sha: detail.result_sha,
        revision: detail.revision,
        ...(action === 'approve' ? { acknowledge_sensitive: options.acknowledgeSensitive === true } : {}),
      })
      setDetail(updated)
      setApprovals((current) => current.map(
        (item) => (item.id === updated.id ? { ...item, status: updated.status } : item),
      ))
    } catch (error) {
      setDecisionError(message(error, '요청을 처리하지 못했습니다.'))
      // 화면이 오래된 상태를 보고 있었을 수 있으므로 현재 내용을 다시 읽는다.
      void loadDetail(detail.id)
    } finally {
      setDeciding(false)
    }
  }, [detail, deciding, loadDetail])

  const hasRunning = useMemo(
    () => approvals.some((item) => isApprovalInProgress(item.status)),
    [approvals],
  )
  const detailRunning = detail !== null && isApprovalInProgress(detail.status)

  // 적용 대기·적용 중인 승인이 있을 때만 주기적으로 다시 읽는다.
  useEffect(() => {
    if (listState !== 'ready' || (!hasRunning && !detailRunning)) return
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const page = await fetchList()
          setApprovals((current) => {
            const refreshed = new Map(page.map((item) => [item.id, item]))
            const patched = current.map((item) => refreshed.get(item.id) ?? item)
            const known = new Set(current.map((item) => item.id))
            const added = page.filter((item) => !known.has(item.id))
            return added.length > 0 ? [...added, ...patched] : patched
          })
        } catch {
          // 주기 갱신 실패는 보고 있는 화면을 지우지 않는다.
        }
        if (selectedId !== null && detailRunning) await loadDetail(selectedId)
      })()
    }, REFRESH_INTERVAL)
    return () => window.clearInterval(timer)
  }, [detailRunning, fetchList, hasRunning, listState, loadDetail, selectedId])

  return {
    approvals,
    listState,
    listError,
    reload,
    statusFilter,
    setStatusFilter,
    selectedId,
    select,
    detail,
    detailError,
    deciding,
    decisionError,
    decide,
  }
}
