import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EmployeeCharacter } from './EmployeeCharacter'

describe('SVG 직원 캐릭터', () => {
  it('팀 색상의 옷을 입고 대기 중에는 의자에 앉는다', () => {
    render(<EmployeeCharacter teamColor="#5f56d8" state="seated" />)

    const character = screen.getByTestId('employee-character')
    expect(character).toHaveAttribute('data-character-state', 'seated')
    expect(character).toHaveStyle({ '--character-shirt': '#5f56d8' })
    expect(character.querySelector('[data-part="chair"]')).toBeInTheDocument()
  })

  it('작업 중에는 의자 없이 걷는 상태를 표시한다', () => {
    render(<EmployeeCharacter teamColor="#7047eb" state="walking" />)

    const character = screen.getByTestId('employee-character')
    expect(character).toHaveAttribute('data-character-state', 'walking')
    expect(character.querySelector('[data-part="chair"]')).not.toBeInTheDocument()
  })

  it('검토 중에는 의자 없이 PM 위치에 머무는 상태를 표시한다', () => {
    render(<EmployeeCharacter teamColor="#7047eb" state="reviewing" />)

    const character = screen.getByTestId('employee-character')
    expect(character).toHaveAttribute('data-at-pm', 'true')
    expect(character.querySelector('[data-part="chair"]')).not.toBeInTheDocument()
  })
})
