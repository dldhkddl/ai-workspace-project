import type { CSSProperties } from 'react'

export type CharacterState = 'seated' | 'walking' | 'reviewing' | 'approval' | 'completed' | 'failed'

interface EmployeeCharacterProps {
  teamColor: string
  state: CharacterState
}

export function EmployeeCharacter({ teamColor, state }: EmployeeCharacterProps) {
  const isSeated = state !== 'walking' && state !== 'reviewing'

  return (
    <svg
      className="employee-character"
      data-testid="employee-character"
      data-character-state={state}
      data-at-pm={state === 'reviewing' ? 'true' : undefined}
      style={{ '--character-shirt': teamColor } as CSSProperties}
      viewBox="0 0 100 130"
      aria-hidden="true"
    >
      <ellipse className="employee-character__shadow" cx="50" cy="123" rx="28" ry="5" />
      {isSeated ? (
        <g data-part="chair" className="employee-character__chair">
          <path d="M28 78h44v28H28z" />
          <path d="M34 103v20M66 103v20" />
        </g>
      ) : null}
      <g data-part="legs" className="employee-character__legs">
        <path className="employee-character__leg employee-character__leg--left" d={isSeated ? 'M43 87l-8 25' : 'M43 87l-5 34'} />
        <path className="employee-character__leg employee-character__leg--right" d={isSeated ? 'M57 87l8 25' : 'M57 87l5 34'} />
      </g>
      <path data-part="body" className="employee-character__body" d="M31 52Q50 42 69 52l5 36Q50 98 26 88z" />
      <g data-part="arms" className="employee-character__arms">
        <g className="employee-character__arm employee-character__arm--left">
          <path d={isSeated ? 'M33 60Q23 72 26 86' : 'M33 60Q20 75 18 92'} />
          <circle cx={isSeated ? 26 : 18} cy={isSeated ? 87 : 93} r="6" />
        </g>
        <g className="employee-character__arm employee-character__arm--right">
          <path d={isSeated ? 'M67 60Q77 72 74 86' : 'M67 60Q80 75 82 92'} />
          <circle cx={isSeated ? 74 : 82} cy={isSeated ? 87 : 93} r="6" />
        </g>
      </g>
      <g data-part="head" className="employee-character__head">
        <ellipse cx="50" cy="34" rx="21" ry="23" />
        <path className="employee-character__hair" d="M29 32Q29 7 52 8q23 2 19 29l-7-14Q49 28 34 21z" />
        <circle className="employee-character__eye" cx="42" cy="36" r="2" />
        <circle className="employee-character__eye" cx="58" cy="36" r="2" />
        <path className="employee-character__smile" d="M43 47q7 5 14 0" />
      </g>
    </svg>
  )
}
