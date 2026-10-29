import { render } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { employees } from '../data/organization'
import { EmployeeCard } from './EmployeeCard'

it('말풍선이 보이는 직원 카드를 최상위 레이어 대상으로 표시한다', () => {
  const employee = employees.find((item) => item.id === 'research-factcheck')!
  const { container } = render(
    <EmployeeCard
      employee={employee}
      teamColor="#2563eb"
      characterState="seated"
      workLabel="진행 중"
      selected={false}
      meetingSpeech={{
        activityId: 'activity-1',
        employeeId: employee.id,
        message: '조사 결과를 공유합니다.',
      }}
      onSelect={vi.fn()}
    />,
  )

  expect(container.querySelector('.employee-card')).toHaveClass('employee-card--speaking')
})
