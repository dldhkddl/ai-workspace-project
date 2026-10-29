import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { OfficeError } from './OfficeError'
import { OfficeLoading } from './OfficeLoading'

describe('운영센터 연결 상태 화면', () => {
  it('로딩 상태를 보조 기술에 알린다', () => {
    render(<OfficeLoading />)
    expect(screen.getByRole('status')).toHaveTextContent('운영센터 연결 중')
  })

  it('안전한 오류 메시지와 다시 시도 동작을 제공한다', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    render(<OfficeError message="운영센터 서버에 연결할 수 없습니다." onRetry={onRetry} />)

    expect(screen.getByRole('alert')).toHaveTextContent('운영센터 서버에 연결할 수 없습니다.')
    await user.click(screen.getByRole('button', { name: '다시 시도' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})
