import type { CSSProperties } from 'react'
import type { Employee } from '../types'
import type { MeetingSpeech } from '../officeConversation'
import { getConnectionStatusLabel } from '../status'
import { EmployeeCharacter, type CharacterState } from './EmployeeCharacter'
import { StatusBadge } from './StatusBadge'
import { OfficeMeetingBubble } from './OfficeMeetingBubble'

interface EmployeeCardProps {
  employee: Employee
  teamColor: string
  characterState: CharacterState
  workLabel: string
  selected: boolean
  isPm?: boolean
  teamLabel?: string
  meetingSpeech?: MeetingSpeech | null
  onSelect: (employee: Employee) => void
}

export function EmployeeCard({
  employee,
  teamColor,
  characterState,
  workLabel,
  selected,
  isPm = false,
  teamLabel,
  meetingSpeech = null,
  onSelect,
}: EmployeeCardProps) {
  return (
    <article
      className={`employee-card${selected ? ' employee-card--selected' : ''}${isPm ? ' employee-card--pm' : ''}${meetingSpeech ? ' employee-card--speaking' : ''}`}
      data-employee-seat={employee.id}
      style={{
        '--team-color': teamColor,
      } as CSSProperties}
    >
      <OfficeMeetingBubble speech={meetingSpeech} employeeName={employee.name} />
      <button
        type="button"
        className="employee-card__select"
        data-character-state={characterState}
        style={{ '--team-color': teamColor } as CSSProperties}
        aria-label={`${employee.name} 직원 선택, ${getConnectionStatusLabel(employee.connection.status)}${workLabel === '대기' ? '' : `, ${workLabel}`}`}
        aria-haspopup="dialog"
        aria-expanded={selected}
        aria-controls={selected ? 'employee-detail' : undefined}
        onClick={() => onSelect(employee)}
      >
        <span className="employee-card__team-label" aria-hidden={teamLabel ? undefined : true}>
          {teamLabel ?? '\u00a0'}
        </span>
        <span className="employee-card__desk" aria-hidden="true">
          <span className="employee-character-anchor">
            <EmployeeCharacter teamColor={teamColor} state={characterState} />
          </span>
          <span className="employee-card__monitor" />
        </span>
        <span className="employee-card__content">
          <strong>{employee.name}</strong>
          <StatusBadge status={employee.connection.status} />
          {workLabel !== '대기' ? (
            <span className={`employee-card__work-state employee-card__work-state--${characterState}`}>
              {workLabel}
            </span>
          ) : null}
        </span>
      </button>
    </article>
  )
}
