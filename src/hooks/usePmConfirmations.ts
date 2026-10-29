import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getEmployeeTask, listTasks, TaskApiError, type EmployeeTask, type TaskSummary,
} from '../api/tasks'

const REFRESH_INTERVAL = 3000

const errorMessage = (error: unknown, fallback: string) =>
  error instanceof TaskApiError ? error.message : fallback

export function usePmConfirmations() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<EmployeeTask | null>(null)
  const requestId = useRef(0)

  const fetchPending = useCallback(async () => {
    const page = await listTasks({ employeeId: 'project-manager', limit: 50, offset: 0 })
    return page.filter((task) => task.requires_user_response === true)
  }, [])

  const reload = useCallback(async (showLoading = true) => {
    const currentRequest = (requestId.current += 1)
    if (showLoading) setLoading(true)
    try {
      const pending = await fetchPending()
      if (currentRequest !== requestId.current) return
      setTasks(pending)
      setError(null)
    } catch (caught) {
      if (currentRequest === requestId.current) {
        setError(errorMessage(caught, 'PM 확인 요청을 불러오지 못했습니다.'))
      }
    } finally {
      if (currentRequest === requestId.current) setLoading(false)
    }
  }, [fetchPending])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    const timer = window.setInterval(() => void reload(false), REFRESH_INTERVAL)
    return () => window.clearInterval(timer)
  }, [reload])

  const select = useCallback(async (taskId: string | null) => {
    setSelectedId(taskId)
    setDetail(null)
    setError(null)
    if (taskId === null) return
    try {
      setDetail(await getEmployeeTask(taskId))
    } catch (caught) {
      setError(errorMessage(caught, 'PM 확인 내용을 불러오지 못했습니다.'))
    }
  }, [])

  const resolved = useCallback(() => {
    setSelectedId(null)
    setDetail(null)
    void reload(false)
  }, [reload])

  return { tasks, loading, error, selectedId, detail, reload, select, resolved }
}
