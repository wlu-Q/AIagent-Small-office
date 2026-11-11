import Phaser from 'phaser'
import ethan from '../../public/assets/character/pickup/ethan/frames.json'
import noah from '../../public/assets/character/pickup/noah/frames.json'
import victor from '../../public/assets/character/pickup/victor/frames.json'
import leo from '../../public/assets/character/pickup/leo/frames.json'
import olivia from '../../public/assets/character/pickup/olivia/frames.json'
import chloe from '../../public/assets/character/pickup/chloe/frames.json'
import maya from '../../public/assets/character/pickup/maya/frames.json'
import luna from '../../public/assets/character/pickup/luna/frames.json'
import emma from '../../public/assets/character/pickup/emma/frames.json'

export const employeePickupAssets = { ethan, noah, victor, leo, olivia, chloe, maya, luna, emma }
export type EmployeePickupCharacter = keyof typeof employeePickupAssets
export const hasEmployeePickupAssets = (character: string): character is EmployeePickupCharacter => (
  Object.prototype.hasOwnProperty.call(employeePickupAssets, character)
)
export const pickupAnimationKey = (character: EmployeePickupCharacter) => `${character}-pickup`
export const pickupTextureKey = (character: EmployeePickupCharacter, layer: 'body' | 'effects') => `${character}-pickup-${layer}`

export function loadEmployeePickupAssets(scene: Phaser.Scene) {
  for (const character of Object.keys(employeePickupAssets) as EmployeePickupCharacter[]) {
    const asset = employeePickupAssets[character]
    for (const layer of ['body', 'effects'] as const) {
      scene.load.spritesheet(pickupTextureKey(character, layer), `assets/character/pickup/${character}/${layer}.png`, {
        frameWidth: asset.frameWidth, frameHeight: asset.frameHeight,
      })
    }
  }
}

export function createEmployeePickupAnims(scene: Phaser.Scene) {
  for (const character of Object.keys(employeePickupAssets) as EmployeePickupCharacter[]) {
    const key = pickupAnimationKey(character)
    if (scene.anims.exists(key)) continue
    const asset = employeePickupAssets[character]
    scene.anims.create({
      key,
      frames: scene.anims.generateFrameNumbers(pickupTextureKey(character, 'body'), { start: 0, end: asset.frameCount - 1 }),
      frameRate: asset.frameRate,
      repeat: -1,
    })
  }
}
