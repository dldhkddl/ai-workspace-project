import { useCallback, useEffect, useState } from 'react'
import { OfficeApiError, fetchOffice, type OfficeData } from '../api/office'

type OfficeDataState =
  | { status: 'loading'; data: null; error: null }
  | { status: 'success'; data: OfficeData; error: null }
  | { status: 'error'; data: null; error: string }

export function useOfficeData() {
  const [requestId, setRequestId] = useState(0)
  const [state, setState] = useState<OfficeDataState>({ status: 'loading', data: null, error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ status: 'loading', data: null, error: null })

    fetchOffice(controller.signal)
      .then((data) => setState({ status: 'success', data, error: null }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        const message = error instanceof OfficeApiError
          ? error.message
          : '운영센터 서버에 연결할 수 없습니다.'
        setState({ status: 'error', data: null, error: message })
      })

    return () => controller.abort()
  }, [requestId])

  const retry = useCallback(() => setRequestId((current) => current + 1), [])

  return { ...state, retry }
}
