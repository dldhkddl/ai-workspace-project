import { describe, expect, it } from 'vitest'
import { calculateCharacterPath, calculateMotionDelay } from './characterPath'

describe('직원과 PM 사이 이동 경로', () => {
  it('직원의 가로 중심을 PM 중심에 맞추고 PM 책상 바로 아래에 도착시킨다', () => {
    const path = calculateCharacterPath(
      { left: 100, top: 300, width: 60, height: 80 },
      { left: 400, top: 80, width: 120, height: 70 },
    )

    expect(path).toEqual({ x: 330, y: -146 })
  })

  it('동시 작업자는 서로 다른 PM 도착 레인과 시작 시점을 사용한다', () => {
    const character = { left: 100, top: 300, width: 60, height: 80 }
    const pmDesk = { left: 400, top: 80, width: 120, height: 70 }

    expect(calculateCharacterPath(character, pmDesk, -24).x).toBe(306)
    expect(calculateCharacterPath(character, pmDesk, 24).x).toBe(354)
    expect(new Set([0, 1, 2, 3, 4, 5].map(calculateMotionDelay)).size).toBe(6)
  })
})
