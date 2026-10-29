import { useLayoutEffect, useRef, type CSSProperties } from 'react'
import { calculateCharacterPath, calculateMotionDelay } from '../characterPath'
import { getCharacterPresentation } from '../characterState'
import type { Employee, Team } from '../types'
import type { MeetingSpeech } from '../officeConversation'
import { EmployeeCard } from './EmployeeCard'

interface OpenOfficeFloorProps {
  teams: Team[]
  employees: Employee[]
  selectedTeamId: string
  selectedEmployeeId: string | null
  animationsPaused: boolean
  meetingSpeech: MeetingSpeech | null
  onSelectEmployee: (employee: Employee) => void
}

export function OpenOfficeFloor({
  teams,
  employees,
  selectedTeamId,
  selectedEmployeeId,
  animationsPaused,
  meetingSpeech,
  onSelectEmployee,
}: OpenOfficeFloorProps) {
  const floorRef = useRef<HTMLElement>(null)
  const pmEmployee = employees.find((employee) => employee.id === 'project-manager')!
  const pmTeam = teams.find((team) => team.id === pmEmployee.teamId)!
  const teamOrder = ['operations', 'story-production', 'leadership', 'design', 'analysis', 'development', 'marketing', 'customer-service', 'investment', 'knowledge']
  const orderedTeams = [...teams].sort((teamA, teamB) => teamOrder.indexOf(teamA.id) - teamOrder.indexOf(teamB.id))
  const visibleTeams = selectedTeamId === 'all'
    ? orderedTeams
    : orderedTeams.filter((team) => team.id === selectedTeamId)

  useLayoutEffect(() => {
    const floor = floorRef.current
    if (!floor) return

    const updatePaths = () => {
      const target = floor.querySelector<HTMLElement>('.open-office__pm-target')
      if (!target) return
      const targetRect = target.getBoundingClientRect()
      const seats = Array.from(floor.querySelectorAll<HTMLElement>('[data-employee-seat]'))
        .filter((seat) => seat.dataset.employeeSeat !== 'project-manager')
      const activeSeats = seats.filter((seat) => {
        const state = seat.querySelector<HTMLElement>('.employee-card__select')?.dataset.characterState
        return state === 'walking' || state === 'reviewing'
      })

      seats.forEach((seat) => {
        const anchor = seat.querySelector<HTMLElement>('.employee-character-anchor')
        if (!anchor) return
        const activeIndex = activeSeats.indexOf(seat)
        const laneOffset = activeIndex >= 0 ? (activeIndex - (activeSeats.length - 1) / 2) * 24 : 0
        const path = calculateCharacterPath(anchor.getBoundingClientRect(), targetRect, laneOffset)
        seat.style.setProperty('--walk-x', `${path.x}px`)
        seat.style.setProperty('--walk-y', `${path.y}px`)
        seat.style.setProperty('--walk-delay', `${calculateMotionDelay(Math.max(activeIndex, 0))}s`)
      })
    }

    updatePaths()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(updatePaths)
    observer.observe(floor)
    return () => observer.disconnect()
  }, [employees, selectedTeamId])

  const renderEmployee = (employee: Employee, team: Team, isPm = false, teamLabel?: string) => {
    const presentation = getCharacterPresentation(employee.work.status)

    return (
      <EmployeeCard
        key={employee.id}
        employee={employee}
        teamColor={team.color}
        characterState={presentation.state}
        workLabel={presentation.label}
        selected={selectedEmployeeId === employee.id}
        isPm={isPm}
        teamLabel={teamLabel}
        meetingSpeech={meetingSpeech?.employeeId === employee.id ? meetingSpeech : null}
        onSelect={onSelectEmployee}
      />
    )
  }

  const renderTeam = (team: Team, priority = false) => {
    const teamEmployees = team.employeeIds
      .filter((id) => id !== pmEmployee.id)
      .map((id) => employees.find((employee) => employee.id === id))
      .filter((employee): employee is Employee => Boolean(employee))
    if (teamEmployees.length === 0) return null

    return (
      <section
        className={`open-office__team-group${priority ? ' open-office__team-group--priority' : ''}`}
        key={team.id}
        aria-label={`${team.name} 좌석`}
      >
        <h2 style={{ '--team-color': team.color } as CSSProperties}>{team.name}</h2>
        <div className="open-office__seats">
          {teamEmployees.map((employee, index) => renderEmployee(employee, team, false, index === 0 ? team.name : undefined))}
        </div>
      </section>
    )
  }


  return (
    <section
      ref={floorRef}
      className={`open-office${animationsPaused ? ' open-office--paused' : ''}`}
      aria-label="AI 직원 열린 사무실"
    >
      <div className="open-office__wall" aria-hidden="true">
        <span>샘플업체 AI COMPANY</span>
      </div>

      <div className="open-office__pm-station">
        <span className="open-office__pm-label">PM 자리</span>
        {renderEmployee(pmEmployee, pmTeam, true)}
        <span className="open-office__pm-target" aria-hidden="true" />
      </div>

      {visibleTeams.length > 0 ? (
        <div className="open-office__teams">
          {visibleTeams.map((team) => renderTeam(team, team.id === 'operations'))}
        </div>
      ) : null}
    </section>
  )
}
