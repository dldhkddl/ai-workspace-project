import { Building2, Maximize2, Pause, Play } from 'lucide-react'
import { useMemo, useState } from 'react'
import { latestMeetingSpeech } from '../officeConversation'
import type { Activity, Employee, Team } from '../types'
import { OpenOfficeFloor } from './OpenOfficeFloor'

interface OfficeProps {
  teams: Team[]
  employees: Employee[]
  selectedTeamId: string
  selectedEmployeeId: string | null
  activities: Activity[]
  onSelectTeam: (teamId: string) => void
  onSelectEmployee: (employee: Employee) => void
}

export function Office({
  teams,
  employees,
  selectedTeamId,
  selectedEmployeeId,
  activities,
  onSelectTeam,
  onSelectEmployee,
}: OfficeProps) {
  const [animationsPaused, setAnimationsPaused] = useState(false)
  const meetingSpeech = useMemo(() => latestMeetingSpeech(activities), [activities])

  return (
    <main id="main-content" className="office" tabIndex={-1}>
      <div className="office__toolbar" aria-label="사무실 보기 설정">
        <div className="office__title">
          <Building2 aria-hidden="true" size={18} />
          <span>팀 바로가기</span>
          <button
            type="button"
            className="office__motion-toggle"
            aria-pressed={animationsPaused}
            onClick={() => setAnimationsPaused((paused) => !paused)}
          >
            {animationsPaused ? <Play aria-hidden="true" size={13} /> : <Pause aria-hidden="true" size={13} />}
            {animationsPaused ? '캐릭터 동작 재생' : '캐릭터 동작 일시정지'}
          </button>
        </div>
        <div className="team-filter">
          <button type="button" aria-pressed={selectedTeamId === 'all'} onClick={() => onSelectTeam('all')}>
            <Maximize2 aria-hidden="true" size={15} />
            전체 보기
          </button>
          {teams.map((team) => (
            <button
              key={team.id}
              type="button"
              aria-label={`${team.shortName}만 보기`}
              aria-pressed={selectedTeamId === team.id}
              onClick={() => onSelectTeam(team.id)}
            >
              <span className="team-filter__dot" style={{ backgroundColor: team.color }} aria-hidden="true" />
              {team.shortName}
            </button>
          ))}
        </div>
      </div>

      <OpenOfficeFloor
        teams={teams}
        employees={employees}
        selectedTeamId={selectedTeamId}
        selectedEmployeeId={selectedEmployeeId}
        animationsPaused={animationsPaused}
        meetingSpeech={meetingSpeech}
        onSelectEmployee={onSelectEmployee}
      />
    </main>
  )
}
