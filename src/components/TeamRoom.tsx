import { useLayoutEffect, useRef } from 'react'
import type { Employee, Team } from '../types'
import { formatConnectionSummary } from '../status'
import { calculateCharacterPath, calculateMotionDelay } from '../characterPath'
import { getCharacterPresentation } from '../characterState'
import { EmployeeCard } from './EmployeeCard'

interface TeamRoomProps {
  team: Team
  employees: Employee[]
  selectedEmployeeId: string | null
  animationsPaused: boolean
  onSelectEmployee: (employee: Employee) => void
}

export function TeamRoom({
  team,
  employees,
  selectedEmployeeId,
  animationsPaused,
  onSelectEmployee,
}: TeamRoomProps) {
  const roomRef = useRef<HTMLElement>(null)

  useLayoutEffect(() => {
    const room = roomRef.current
    if (!room) return

    const updatePaths = () => {
      const pmDesk = room.querySelector<HTMLElement>('.team-room__pm-desk')
      if (!pmDesk) return
      const pmRect = pmDesk.getBoundingClientRect()

      const seats = Array.from(room.querySelectorAll<HTMLElement>('[data-employee-seat]'))
      const activeSeats = seats.filter((seat) => {
        const state = seat.querySelector<HTMLElement>('.employee-card__select')?.dataset.characterState
        return state === 'walking' || state === 'reviewing'
      })

      seats.forEach((seat) => {
        const anchor = seat.querySelector<HTMLElement>('.employee-character-anchor')
        if (!anchor) return
        const activeIndex = activeSeats.indexOf(seat)
        const laneOffset = activeIndex >= 0 ? (activeIndex - (activeSeats.length - 1) / 2) * 24 : 0
        const path = calculateCharacterPath(anchor.getBoundingClientRect(), pmRect, laneOffset)
        seat.style.setProperty('--walk-x', `${path.x}px`)
        seat.style.setProperty('--walk-y', `${path.y}px`)
        seat.style.setProperty('--walk-delay', `${calculateMotionDelay(Math.max(activeIndex, 0))}s`)
      })
    }

    updatePaths()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(updatePaths)
    observer.observe(room)
    return () => observer.disconnect()
  }, [employees])

  return (
    <section
      ref={roomRef}
      className={`team-room${animationsPaused ? ' team-room--paused' : ''}`}
      aria-label={`${team.name} 팀 사무실`}
      style={{ '--team-color': team.color } as React.CSSProperties}
    >
      <header className="team-room__header">
        <div>
          <p className="team-room__eyebrow">{team.shortName}</p>
          <h2>{team.name}</h2>
        </div>
        <span className="team-room__summary">{formatConnectionSummary(employees)}</span>
      </header>
      <div className="team-room__office-floor">
        <div className="team-room__pm-desk" aria-label={`${team.name} PM 자리`}>
          <span className="team-room__pm-monitor" aria-hidden="true" />
          <strong>PM 자리</strong>
        </div>
        <div className="team-room__desks">
        {employees.map((employee) => {
          const presentation = getCharacterPresentation(employee.work.status)

          return (
            <EmployeeCard
              key={employee.id}
              employee={employee}
              teamColor={team.color}
              characterState={presentation.state}
              workLabel={presentation.label}
              selected={selectedEmployeeId === employee.id}
              onSelect={onSelectEmployee}
            />
          )
        })}
        </div>
      </div>
    </section>
  )
}
