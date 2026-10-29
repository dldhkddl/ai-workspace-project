import { describe, expect, it } from 'vitest'
import { getCharacterPresentation } from './characterState'
import type { WorkStatus } from './types'

describe('업무 상태별 캐릭터 표현', () => {
  it.each<[WorkStatus, string, string]>([
    [null, 'seated', '대기'],
    ['idle', 'seated', '대기'],
    ['working', 'walking', '작업 중'],
    ['reviewing', 'reviewing', '검토 중'],
    ['awaiting_approval', 'approval', '승인 대기'],
    ['completed', 'completed', '완료'],
    ['failed', 'failed', '실패'],
  ])('%s 상태를 %s 자세와 %s 문구로 변환한다', (status, state, label) => {
    expect(getCharacterPresentation(status)).toEqual({ state, label })
  })
})
