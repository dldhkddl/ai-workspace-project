import { useEffect, useMemo, useRef, useState } from 'react'
import { ActivityPanel } from './components/ActivityPanel'
import { EmployeeDetail } from './components/EmployeeDetail'
import { EmptySection } from './components/EmptySection'
import { Header } from './components/Header'
import { Navigation, type ViewId } from './components/Navigation'
import { Office } from './components/Office'
import { ApprovalCenter } from './components/ApprovalCenter'
import { TaskHistory } from './components/TaskHistory'
import { OfficeError } from './components/OfficeError'
import { OfficeLoading } from './components/OfficeLoading'
import { MeetingSummaryPanel } from './components/MeetingSummaryPanel'
import { useOfficeData } from './hooks/useOfficeData'
import { useOperationsFeed } from './hooks/useOperationsFeed'
import type { Employee } from './types'

export default function App() {
  const officeData = useOfficeData()
  const operationsFeed = useOperationsFeed()
  const [activeView, setActiveView] = useState<ViewId>('office')
  const [selectedTeamId, setSelectedTeamId] = useState('all')
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string | null>(null)
  const [focusedMeetingTaskId, setFocusedMeetingTaskId] = useState<string | null>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const teams = officeData.data?.teams ?? []
  const officeEmployees = officeData.data?.employees ?? []
  const employees = useMemo(() => officeEmployees.map((employee) => ({
    ...employee,
    alerts: Array.from(new Set([
      ...employee.alerts,
      ...operationsFeed.alerts
        .filter((alert) => alert.employeeId === employee.id)
        .map((alert) => alert.message),
    ])),
  })), [officeEmployees, operationsFeed.alerts])

  const selectedEmployee = useMemo(
    () => employees.find((employee) => employee.id === selectedEmployeeId) ?? null,
    [employees, selectedEmployeeId],
  )
  const selectedTeam = selectedEmployee
    ? teams.find((team) => team.id === selectedEmployee.teamId) ?? null
    : null
  const employeeNames = useMemo(
    () => new Map(employees.map((employee) => [employee.id, employee.name])),
    [employees],
  )

  useEffect(() => {
    if (selectedEmployeeId && !employees.some((employee) => employee.id === selectedEmployeeId)) {
      setSelectedEmployeeId(null)
    }
  }, [employees, selectedEmployeeId])

  useEffect(() => {
    if (selectedTeamId !== 'all' && !teams.some((team) => team.id === selectedTeamId)) {
      setSelectedTeamId('all')
    }
  }, [selectedTeamId, teams])

  const handleSelectEmployee = (employee: Employee) => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setSelectedEmployeeId(employee.id)
  }

  const handleCloseDetail = () => {
    const previousFocus = previousFocusRef.current
    setSelectedEmployeeId(null)
    queueMicrotask(() => previousFocus?.focus())
  }

  const handleSelectTeam = (teamId: string) => {
    setSelectedTeamId(teamId)
    if (teamId !== 'all' && selectedEmployee?.teamId !== teamId) {
      setSelectedEmployeeId(null)
    }
  }

  const handlePmResponseSubmitted = (taskId: string) => {
    setFocusedMeetingTaskId(taskId)
    setSelectedEmployeeId(null)
    setActiveView('office')
  }

  if (officeData.status === 'loading') {
    return <div className="app-shell"><OfficeLoading /></div>
  }

  if (officeData.status === 'error') {
    return (
      <div className="app-shell">
        <OfficeError message={officeData.error} onRetry={officeData.retry} />
      </div>
    )
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
      <Header employees={employees} />
      <Navigation activeView={activeView} onChange={setActiveView} />

      {activeView === 'office' ? (
        <>
          <div className="workspace">
            <Office
              teams={teams}
              employees={employees}
              selectedTeamId={selectedTeamId}
              selectedEmployeeId={selectedEmployeeId}
              activities={operationsFeed.activities}
              onSelectTeam={handleSelectTeam}
              onSelectEmployee={handleSelectEmployee}
            />
            <MeetingSummaryPanel
              activities={operationsFeed.activities}
              focusedTaskId={focusedMeetingTaskId}
              employeeNames={employeeNames}
            />
            {selectedEmployee && selectedTeam ? (
              <>
                <div className="detail-scrim" aria-hidden="true" onClick={handleCloseDetail} />
                <EmployeeDetail
                  employee={selectedEmployee}
                  team={selectedTeam}
                  employeeNames={employeeNames}
                  onClose={handleCloseDetail}
                />
              </>
            ) : null}
          </div>
          <ActivityPanel {...operationsFeed} />
        </>
      ) : activeView === 'tasks' ? (
        <TaskHistory employees={employees} />
      ) : activeView === 'approvals' ? (
        <ApprovalCenter employeeNames={employeeNames} onPmResponseSubmitted={handlePmResponseSubmitted} />
      ) : (
        <EmptySection view={activeView} />
      )}
    </div>
  )
}
