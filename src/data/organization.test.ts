import { describe, expect, it } from 'vitest'
import { employees, teams } from './organization'

describe('AI 운영센터 조직 데이터', () => {
  it('10개 팀에 26명의 직원을 빠짐없이 배치한다', () => {
    expect(teams).toHaveLength(10)
    expect(employees).toHaveLength(26)
    expect(new Set(employees.map((employee) => employee.id)).size).toBe(26)

    const assignedEmployeeIds = teams.flatMap((team) => team.employeeIds)
    expect(assignedEmployeeIds).toHaveLength(26)
    expect(new Set(assignedEmployeeIds).size).toBe(26)
    expect(teams.find((team) => team.id === 'story-production')).toMatchObject({
      name: 'AI 스토리 제작팀',
      employeeIds: ['scenario-writer', 'story-editor', 'prompt-director', 'shorts-producer'],
    })
  })

  it('연동 전 직원에게 가짜 업무 데이터를 넣지 않는다', () => {
    for (const employee of employees) {
      expect(employee.connection.status).toBe('not_connected')
      expect(employee.work).toEqual({
        status: null,
        taskId: null,
        title: null,
        stage: null,
        progress: null,
      })
      expect(employee.summary).toBeNull()
      expect(employee.alerts).toEqual([])
    }
  })
})
