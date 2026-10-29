export interface CharacterRect {
  left: number
  top: number
  width: number
  height: number
}

export function calculateCharacterPath(character: CharacterRect, pmDesk: CharacterRect, laneOffset = 0) {
  const characterCenterX = character.left + character.width / 2
  const pmCenterX = pmDesk.left + pmDesk.width / 2

  return {
    x: Math.round(pmCenterX + laneOffset - characterCenterX),
    y: Math.round(pmDesk.top + pmDesk.height + 4 - character.top),
  }
}

export function calculateMotionDelay(index: number) {
  return Number((-index * 0.73).toFixed(2))
}
