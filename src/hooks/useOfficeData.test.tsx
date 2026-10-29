import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OfficeApiError, fetchOffice } from '../api/office'
import { useOfficeData } from './useOfficeData'

vi.mock('../api/office', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/office')>()
  return { ...actual, fetchOffice: vi.fn() }
})

const mockedFetchOffice = vi.mocked(fetchOffice)
const office = { teams: [], employees: [] }

describe('useOfficeData', () => {
  beforeEach(() => mockedFetchOffice.mockReset())

  it('첫 요청 동안 로딩 상태를 제공하고 성공 데이터를 저장한다', async () => {
    mockedFetchOffice.mockResolvedValue(office)

    const { result } = renderHook(() => useOfficeData())

    expect(result.current.status).toBe('loading')
    await waitFor(() => expect(result.current.status).toBe('success'))
    expect(result.current.data).toEqual(office)
  })

  it('실패 후 재시도하면 새 요청으로 정상 데이터를 불러온다', async () => {
    mockedFetchOffice
      .mockRejectedValueOnce(new OfficeApiError('OFFICE_CONNECTION_FAILED', '운영센터 서버에 연결할 수 없습니다.'))
      .mockResolvedValueOnce(office)

    const { result } = renderHook(() => useOfficeData())

    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.error).toBe('운영센터 서버에 연결할 수 없습니다.')
    act(() => result.current.retry())
    expect(result.current.status).toBe('loading')
    await waitFor(() => expect(result.current.status).toBe('success'))
    expect(mockedFetchOffice).toHaveBeenCalledTimes(2)
  })
})
