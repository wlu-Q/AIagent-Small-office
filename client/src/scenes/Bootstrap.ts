import Phaser from 'phaser'
import { additionalCharacterTextures } from '../characters/additionalCharacterTextures'
import { DAILY_FRAME_WIDTH, DAILY_FRAME_HEIGHT, DAILY_FOOT_Y, DAILY_LOGICAL_WIDTH, DAILY_LOGICAL_HEIGHT, dailyCharacterImage, dailyFrameOffset } from '../characters/dailyCharacterAssets'
import { loadEmployeeReactionAssets } from '../anims/EmployeeReactionAnims'
import { loadEmployeePickupAssets } from '../anims/EmployeePickupAnims'

export default class Bootstrap extends Phaser.Scene {
  private preloadComplete = false

  constructor() {
    super('bootstrap')
  }

  preload() {
    this.load.atlas(
      'cloud_day',
      'assets/background/cloud_day.png',
      'assets/background/cloud_day.json'
    )
    this.load.image('backdrop_day', 'assets/background/backdrop_day.png')
    this.load.atlas(
      'cloud_night',
      'assets/background/cloud_night.png',
      'assets/background/cloud_night.json'
    )
    this.load.image('backdrop_night', 'assets/background/backdrop_night.png')
    this.load.image('sun_moon', 'assets/background/sun_moon.png')

    this.load.tilemapTiledJSON('tilemap', 'assets/map/map.json')
    this.load.spritesheet('tiles_wall', 'assets/map/FloorAndGround.png', {
      frameWidth: 32,
      frameHeight: 32,
    })
    this.load.spritesheet('chairs', 'assets/items/chair.png', {
      frameWidth: 32,
      frameHeight: 64,
    })
    this.load.spritesheet('computers', 'assets/items/computer.png', {
      frameWidth: 96,
      frameHeight: 64,
    })
    this.load.spritesheet('whiteboards', 'assets/items/whiteboard.png', {
      frameWidth: 64,
      frameHeight: 64,
    })
    this.load.spritesheet('vendingmachines', 'assets/items/vendingmachine.png', {
      frameWidth: 48,
      frameHeight: 72,
    })
    this.load.spritesheet('office', 'assets/tileset/Modern_Office_Black_Shadow.png', {
      frameWidth: 32,
      frameHeight: 32,
    })
    this.load.spritesheet('basement', 'assets/tileset/Basement.png', {
      frameWidth: 32,
      frameHeight: 32,
    })
    this.load.spritesheet('generic', 'assets/tileset/Generic.png', {
      frameWidth: 32,
      frameHeight: 32,
    })
    this.load.spritesheet('adam', 'assets/character/adam.png', {
      frameWidth: 32,
      frameHeight: 48,
    })
    this.load.spritesheet('ash', 'assets/character/ash.png', {
      frameWidth: 32,
      frameHeight: 48,
    })
    this.load.spritesheet('lucy', 'assets/character/lucy.png', {
      frameWidth: 32,
      frameHeight: 48,
    })
    this.load.spritesheet('nancy', 'assets/character/nancy.png', {
      frameWidth: 32,
      frameHeight: 48,
    })
    additionalCharacterTextures.forEach((texture) => {
      this.load.image(texture, dailyCharacterImage(texture))
    })
    loadEmployeeReactionAssets(this)
    loadEmployeePickupAssets(this)

    this.load.on('complete', () => {
      additionalCharacterTextures.forEach(texture => {
        for (let index = 0; index < 52; index++) {
          const offset = dailyFrameOffset(index)
          this.textures.get(texture).add(index, 0,
            offset.x, offset.y,
            DAILY_FRAME_WIDTH, DAILY_FRAME_HEIGHT,
          ).setTrim(
            DAILY_LOGICAL_WIDTH, DAILY_LOGICAL_HEIGHT,
            (DAILY_LOGICAL_WIDTH - DAILY_FRAME_WIDTH) / 2, DAILY_LOGICAL_HEIGHT - DAILY_FOOT_Y,
            DAILY_FRAME_WIDTH, DAILY_FRAME_HEIGHT,
          )
        }
      })
      this.preloadComplete = true
      this.scene.launch('background')
      this.launchGame()
    })
  }

  launchGame() {
    if (!this.preloadComplete) return
    this.scene.launch('game')
  }

}
