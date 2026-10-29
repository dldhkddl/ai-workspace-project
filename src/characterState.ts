import type { CharacterState } from './components/EmployeeCharacter'
import type { WorkStatus } from './types'

export interface CharacterPresentation {
  state: CharacterState
  label: string
}

const presentations: Record<Exclude<WorkStatus, null>, CharacterPresentation> = {
  idle: { state: 'seated', label: '대기' },
  working: { state: 'walking', label: '작업 중' },
  reviewing: { state: 'reviewing', label: '검토 중' },
  awaiting_approval: { state: 'approval', label: '승인 대기' },
  completed: { state: 'completed', label: '완료' },
  failed: { state: 'failed', label: '실패' },
}

export function getCharacterPresentation(status: WorkStatus): CharacterPresentation {
  return status === null ? { state: 'seated', label: '대기' } : presentations[status]
}
