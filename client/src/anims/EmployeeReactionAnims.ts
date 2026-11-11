import Phaser from 'phaser'
import catalog from '../../public/assets/character/reactions/catalog.json'
import type { EmployeeReactionAction } from '../characters/EmployeeInteraction'

export type EmployeeReactionCharacter = keyof typeof catalog
type ReactionFrame = {
  source: number[]
  anchorX: number
  anchorY: number
  bodyBounds: number[]
  contentTop: number
  visibleHeight: number
}
type ReactionAsset = {
  image: string
  imageSha256: string
  scale: number
  frames: ReactionFrame[]
  steps: { frame: number; duration: number }[]
  sourceFps?: number
  playbackFps?: number
}

export const employeeReactionAssets: Record<EmployeeReactionCharacter, Record<EmployeeReactionAction, ReactionAsset>> = catalog
export const hasEmployeeReactionAssets = (texture: string): texture is EmployeeReactionCharacter => (
  Object.prototype.hasOwnProperty.call(employeeReactionAssets, texture)
)
export const reactionAnimationKey = (character: EmployeeReactionCharacter, action: EmployeeReactionAction) => (
  `${character}_reaction_${action}`
)
export const reactionTextureKey = (character: EmployeeReactionCharacter, action: EmployeeReactionAction) => (
  `${character}-reaction-${employeeReactionAssets[character][action].image}`
)
export const reactionFrameKey = (action: EmployeeReactionAction, frame: number) => `${action}-${frame}`

export function loadEmployeeReactionAssets(scene: Phaser.Scene) {
  const loaded = new Set<string>()
  for (const character of Object.keys(employeeReactionAssets) as EmployeeReactionCharacter[]) {
    for (const action of Object.keys(employeeReactionAssets[character]) as EmployeeReactionAction[]) {
      const asset = employeeReactionAssets[character][action]
      const texture = reactionTextureKey(character, action)
      if (loaded.has(texture)) continue
      loaded.add(texture)
      scene.load.image(texture, `assets/character/reactions/${character}/${asset.image}.png`)
    }
  }
}

// Preserve the approved pixels, frame timings and individual foot anchors.
export function createEmployeeReactionAnims(scene: Phaser.Scene) {
  for (const character of Object.keys(employeeReactionAssets) as EmployeeReactionCharacter[]) {
    for (const action of Object.keys(employeeReactionAssets[character]) as EmployeeReactionAction[]) {
      const key = reactionAnimationKey(character, action)
      if (scene.anims.exists(key)) continue
      const asset = employeeReactionAssets[character][action]
      const textureKey = reactionTextureKey(character, action)
      const texture = scene.textures.get(textureKey)
      asset.frames.forEach((frame, index) => {
        const [x, y, width, height] = frame.source
        texture.add(reactionFrameKey(action, index), 0, x, y, width, height)
      })
      scene.anims.create({
        key,
        frames: asset.steps.map(step => ({
          key: textureKey, frame: reactionFrameKey(action, step.frame), duration: step.duration - 40,
        })),
        frameRate: 25,
        repeat: 0,
      })
    }
  }
}
