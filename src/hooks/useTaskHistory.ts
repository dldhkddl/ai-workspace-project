import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  getEmployeeTask,
  isTerminalTaskStatus,
  listTasks,
  TaskApiError,
  type EmployeeTask,
  type TaskStatus,
  type TaskSummary,
} from '../api/tasks'

export const TASK_PAGE_SIZE = 50
const REFRESH_INTERVAL = 3000

export type TaskListState = 'loading' | 'ready' | 'error'
export type TaskDetailState = 'idle' | 'loading' | 'ready' | 'error'
export type TaskStatusFilter = TaskStatus | 'all'

const errorMessage = (error: unknown, fallback: string) =>
  error instanceof TaskApiError ? error.message : fallback

export function useTaskHistory() {
  const [statusFilter, setStatusFilter] = useState<TaskStatusFilter>('all')
  const [employeeFilter, setEmployeeFilter] = useState<string>('all')

  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [listState, setListState] = useState<TaskListState>('loading')
  const [listError, setListError] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<EmployeeTask | null>(null)
  const [detailState, setDetailState] = useState<TaskDetailState>('idle')
  const [detailError, setDetailError] = useState<string | null>(null)

  // 상세는 작업 ID 기준으로 캐시한다. 끝난 작업은 다시 불러오지 않는다.
  const detailCache = useRef(new Map<string, EmployeeTask>())
  const listRequestId = useRef(0)
  const detailRequestId = useRef(0)

  const fetchPage = useCallback((offset: number) => listTasks({
    status: statusFilter === 'all' ? null : statusFilter,
    employeeId: employeeFilter === 'all' ? null : employeeFilter,
    limit: TASK_PAGE_SIZE,
    offset,
  }), [statusFilter, employeeFilter])

  const reload = useCallback(async () => {
    const requestId = (listRequestId.current += 1)
    setListState('loading')
    setListError(null)
    try {
      const page = await fetchPage(0)
      if (requestId !== listRequestId.current) return
      setTasks(page)
      setHasMore(page.length === TASK_PAGE_SIZE)
      setListState('ready')
    } catch (error) {
      if (requestId !== listRequestId.current) return
      setListError(errorMessage(error, '작업 기록을 불러오지 못했습니다.'))
      setListState('error')
    }
  }, [fetchPage])

  useEffect(() => {
    void reload()
  }, [reload])

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)
    try {
      const page = await fetchPage(tasks.length)
      // 페이지를 넘기는 사이 새 작업이 앞에 끼어들면 항목이 밀리므로 ID로 중복을 걸러낸다.
      const known = new Set(tasks.map((task) => task.id))
      setTasks([...tasks, ...page.filter((task) => !known.has(task.id))])
      setHasMore(page.length === TASK_PAGE_SIZE)
      setListError(null)
    } catch (error) {
      setListError(errorMessage(error, '작업을 더 불러오지 못했습니다.'))
    } finally {
      setLoadingMore(false)
    }
  }, [fetchPage, hasMore, loadingMore, tasks])

  const loadDetail = useCallback(async (taskId: string) => {
    const requestId = (detailRequestId.current += 1)
    setDetailState('loading')
    setDetailError(null)
    try {
      const task = await getEmployeeTask(taskId)
      if (requestId !== detailRequestId.current) return
      detailCache.current.set(taskId, task)
      setDetail(task)
      setDetailState('ready')
    } catch (error) {
      if (requestId !== detailRequestId.current) return
      setDetailError(errorMessage(error, '작업 결과를 불러오지 못했습니다.'))
      setDetailState('error')
    }
  }, [])

  const select = useCallback((taskId: string | null) => {
    setSelectedId(taskId)
    if (taskId === null) {
      detailRequestId.current += 1
      setDetail(null)
      setDetailState('idle')
      setDetailError(null)
      return
    }
    const cached = detailCache.current.get(taskId)
    if (cached && isTerminalTaskStatus(cached.status)) {
      detailRequestId.current += 1
      setDetail(cached)
      setDetailState('ready')
      setDetailError(null)
      return
    }
    setDetail(cached ?? null)
    void loadDetail(taskId)
  }, [loadDetail])

  const retryDetail = useCallback(() => {
    if (selectedId) void loadDetail(selectedId)
  }, [loadDetail, selectedId])

  const hasRunningTask = useMemo(
    () => tasks.some((task) => !isTerminalTaskStatus(task.status)),
    [tasks],
  )

  // 진행 중인 작업이 있을 때만 첫 페이지를 다시 읽어 상태를 덮어쓴다.
  useEffect(() => {
    if (listState !== 'ready' || !hasRunningTask) return
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const page = await fetchPage(0)
          setTasks((current) => {
            const refreshed = new Map(page.map((task) => [task.id, task]))
            const patched = current.map((task) => refreshed.get(task.id) ?? task)
            const known = new Set(current.map((task) => task.id))
            const added = page.filter((task) => !known.has(task.id))
            return added.length > 0 ? [...added, ...patched] : patched
          })
        } catch {
          // 주기 새로고침 실패는 이미 보고 있는 목록을 지우지 않는다.
        }
      })()
    }, REFRESH_INTERVAL)
    return () => window.clearInterval(timer)
  }, [fetchPage, hasRunningTask, listState])

  const detailRunning = detail !== null && !isTerminalTaskStatus(detail.status)

  useEffect(() => {
    if (selectedId === null || !detailRunning) return
    const timer = window.setInterval(() => {
      void loadDetail(selectedId)
    }, REFRESH_INTERVAL)
    return () => window.clearInterval(timer)
  }, [detailRunning, loadDetail, selectedId])

  const changeStatusFilter = useCallback((next: TaskStatusFilter) => {
    setStatusFilter(next)
    setHasMore(false)
  }, [])

  const changeEmployeeFilter = useCallback((next: string) => {
    setEmployeeFilter(next)
    setHasMore(false)
  }, [])

  return {
    tasks,
    listState,
    listError,
    hasMore,
    loadingMore,
    loadMore,
    reload,
    statusFilter,
    employeeFilter,
    changeStatusFilter,
    changeEmployeeFilter,
    selectedId,
    select,
    detail,
    detailState,
    detailError,
    retryDetail,
  }
}
