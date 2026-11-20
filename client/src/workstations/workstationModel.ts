import artworkManifest from '../../public/assets/workstations/manifest.json'
import type { additionalCharacterTextures } from '../characters/additionalCharacterTextures'

export const WORKSTATION_WIDTH = artworkManifest.width
export const WORKSTATION_HEIGHT = artworkManifest.height
export const SCREEN_RECT = artworkManifest.screen
export const WORK_SCREEN_LOOP_MS = artworkManifest.screenFrames.duration
export const WORK_SCREEN_CLIPS = artworkManifest.screenFrames.clips
export type WorkScreenClip = typeof WORK_SCREEN_CLIPS[number]
export type DeskProp = Exclude<keyof typeof artworkManifest.props, 'tower' | 'mouse'>
export type DeskLayout = { tower: boolean; props: [DeskProp, DeskProp, DeskProp] }
export type WorkstationHandStyle = keyof typeof artworkManifest.hands
export const WORKSTATION_HAND_STYLES = {
  ethan: 'navy', noah: 'shirt', victor: 'hoodie', leo: 'hoodie',
  olivia: 'blouse', chloe: 'cardigan', maya: 'suit', luna: 'cardigan', emma: 'blouse',
} as const satisfies Record<typeof additionalCharacterTextures[number], WorkstationHandStyle>

export function handStyleForCharacter(texture?: string): WorkstationHandStyle | undefined {
  if (!texture || !Object.prototype.hasOwnProperty.call(WORKSTATION_HAND_STYLES, texture)) return undefined
  return WORKSTATION_HAND_STYLES[texture as keyof typeof WORKSTATION_HAND_STYLES]
}

export type WorkstationSnapshot = {
  workstationId: string
  computerFrame: number
  employeeId?: string
  displayName?: string
  characterTexture?: string
  state: 'idle' | 'working' | 'disabled' | 'unbound'
  atDesk: boolean
}

export function createDeskLayout(workstationId: string, computerFrame: number): DeskLayout {
  let seed = 2166136261
  for (const character of `${workstationId}:${computerFrame}`) seed = Math.imul(seed ^ character.charCodeAt(0), 16777619) >>> 0
  const choose = <Value,>(values: readonly Value[]): Value => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
    return values[Math.floor(seed / 4294967296 * values.length)]
  }
  return {
    tower: choose([true, false]),
    props: [choose(['plant', 'flowers', 'deskLamp', 'cactus']), choose(['coffee', 'water', 'lemonTea']),
      choose(['figurine', 'tissueBox', 'deskClock', 'pencils', 'radio'])],
  }
}

export function nextScreenClip(previous?: WorkScreenClip, random = Math.random): WorkScreenClip {
  const candidates = WORK_SCREEN_CLIPS.filter(clip => clip !== previous)
  return candidates[Math.min(candidates.length - 1, Math.floor(random() * candidates.length))]
}

export function isOperating(snapshot: WorkstationSnapshot) {
  return Boolean(snapshot.employeeId && snapshot.state === 'working' && snapshot.atDesk)
}

export const HAND_CYCLE_MS = 8400
export const KEY_STROKE_MS = 140

function ease(progress: number) {
  const bounded = Math.max(0, Math.min(1, progress))
  return bounded * bounded * (3 - 2 * bounded)
}

function pressPulse(elapsed: number, duration: number) {
  if (elapsed <= 0 || elapsed >= duration) return 0
  const progress = elapsed / duration
  return progress < 0.35 ? ease(progress / 0.35) : 1 - ease((progress - 0.35) / 0.65)
}

export function handPose(milliseconds: number) {
  const elapsed = milliseconds % HAND_CYCLE_MS
  const rest = { leftX: 0, leftY: 0, leftFrame: 0, rightFrame: 2, mouseX: 0, mouseY: 0, click: false }
  if (elapsed < 2800) {
    const envelope = ease(elapsed / 100) * ease((2800 - elapsed) / 100)
    const beat = KEY_STROKE_MS * 2
    const leftPressure = pressPulse(elapsed % beat, 240) * envelope
    const rightPressure = pressPulse((elapsed + KEY_STROKE_MS) % beat, 240) * envelope
    const alternate = Math.floor(elapsed / beat) % 2
    return {
      ...rest, phase: 'typing', rightX: -111, rightY: -9 + rightPressure * 0.7,
      leftX: Math.sin(elapsed / 700 * Math.PI) * envelope * 0.7, leftY: leftPressure * 0.7,
      leftFrame: envelope < 0.2 ? 0 : alternate * 2 + (leftPressure > 0.4 ? 1 : 0),
      rightFrame: rightPressure > 0.4 ? 1 : 0,
    }
  }
  if (elapsed < 3500) {
    const progress = (elapsed - 2800) / 700
    const travel = ease(progress)
    return { ...rest, phase: 'reach', rightX: -111 * (1 - travel), rightY: -9 * (1 - travel) - Math.sin(progress * Math.PI) * 3, rightFrame: progress < 0.65 ? 0 : 2 }
  }
  if (elapsed < 6300) {
    const time = elapsed - 3500
    let mouseX: number, mouseY: number
    if (time < 1120) {
      const travel = ease(time / 700)
      mouseX = 7 * travel; mouseY = -2 * travel
    } else if (time < 2380) {
      const travel = ease((time - 1120) / 780)
      mouseX = 7 - 12 * travel; mouseY = -2 + 5 * travel
    } else {
      const travel = ease((time - 2380) / 420)
      mouseX = -5 * (1 - travel); mouseY = 3 * (1 - travel)
    }
    const pressure = Math.max(...[900, 2020, 2200].map(start => pressPulse(time - start, 180)))
    return { ...rest, phase: 'mouse', rightX: mouseX, rightY: mouseY + pressure * 0.5, mouseX, mouseY, rightFrame: pressure > 0.35 ? 3 : 2, click: pressure > 0.35 }
  }
  if (elapsed < 7400) return { ...rest, phase: 'pause', rightX: 0, rightY: 0 }
  const progress = (elapsed - 7400) / 1000
  const travel = ease(progress)
  return { ...rest, phase: 'return', rightX: -111 * travel, rightY: -9 * travel - Math.sin(progress * Math.PI) * 3, rightFrame: progress < 0.35 ? 2 : 0 }
}
