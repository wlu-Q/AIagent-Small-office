import { additionalCharacterTextures } from './additionalCharacterTextures'

const dailyCharacters = new Set<string>(additionalCharacterTextures)
export const hasDailyCharacterAssets = (texture: string) => dailyCharacters.has(texture)
// Artwork uses a wider transparent gutter; the logical actor stays 32 x 48.
export const DAILY_FRAME_WIDTH = 96
export const DAILY_FRAME_HEIGHT = 114
export const DAILY_FOOT_Y = 112
export const DAILY_LOGICAL_WIDTH = 64
export const DAILY_LOGICAL_HEIGHT = 96
export const DAILY_SCALE = 0.5

export function dailySourceFrame(frame: number) {
  if (frame < 24) return Math.floor(frame / 6) * 8
  if (frame < 48) return Math.floor((frame - 24) / 6) * 8 + 1 + (frame - 24) % 6
  return [31, 23, 7, 15][frame - 48]
}

export function dailyWalkFrames(texture: string, direction: string, start: number) {
  if (texture === 'ethan') return [0, 1, 2, 3, 4, 5].map(frame => start + frame)
  const earlyPassing = ['victor', 'leo', 'maya', 'luna'].includes(texture)
  const phases = direction === 'up' || direction === 'down'
    ? [5, 0, 5, 5, 3, 5]
    : earlyPassing ? [1, 0, 1, 3, 2, 3] : [2, 0, 2, 5, 3, 5]
  return phases.map(frame => start + frame)
}
