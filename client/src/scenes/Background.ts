import Phaser from 'phaser'
import { BackgroundMode } from '../types/BackgroundMode'

export default class Background extends Phaser.Scene {
  private cloud!: Phaser.Physics.Arcade.Group
  private cloudKey!: string
  private backdropKey!: string
  private backdropImage!: Phaser.GameObjects.Image
  private sunMoonImage!: Phaser.GameObjects.Image
  private backgroundMode!: BackgroundMode
  private layoutWidth = 0
  private layoutHeight = 0
  private lastClockCheck = 0

  constructor() {
    super('background')
  }

  create() {
    const sceneHeight = this.cameras.main.height
    const sceneWidth = this.cameras.main.width
    this.backgroundMode = this.getTimeBackgroundMode()
    this.setBackgroundTextures()
    this.lastClockCheck = Date.now()
    this.layoutWidth = sceneWidth
    this.layoutHeight = sceneHeight

    // Add backdrop image
    this.backdropImage = this.add.image(0, 0, this.backdropKey).setScrollFactor(0)

    // Add sun or moon image
    this.sunMoonImage = this.add.image(0, 0, 'sun_moon').setScrollFactor(0)

    // Add 24 clouds at random positions and with random speeds
    const frames = this.textures.get(this.cloudKey).getFrameNames()
    this.cloud = this.physics.add.group()
    for (let i = 0; i < 24; i++) {
      const x = Phaser.Math.RND.between(-sceneWidth * 0.5, sceneWidth * 1.5)
      const y = Phaser.Math.RND.between(sceneHeight * 0.2, sceneHeight * 0.8)
      const velocity = Phaser.Math.RND.between(15, 30)

      this.cloud
        .get(x, y, this.cloudKey, frames[i % 6])
        .setScale(3)
        .setVelocity(velocity, 0)
    }

    this.resizeBackground(this.scale.gameSize)
    this.scale.on(Phaser.Scale.Events.RESIZE, this.resizeBackground, this)
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      this.scale.off(Phaser.Scale.Events.RESIZE, this.resizeBackground, this)
    })
  }

  private getTimeBackgroundMode() {
    const hour = new Date().getHours()
    return hour >= 6 && hour < 18 ? BackgroundMode.DAY : BackgroundMode.NIGHT
  }

  private setBackgroundTextures() {
    const isDay = this.backgroundMode === BackgroundMode.DAY
    this.backdropKey = isDay ? 'backdrop_day' : 'backdrop_night'
    this.cloudKey = isDay ? 'cloud_day' : 'cloud_night'
    this.cameras.main.setBackgroundColor(isDay ? '#c6eefc' : '#2c4464')
  }

  private resizeBackground(gameSize: Phaser.Structs.Size) {
    const { width, height } = gameSize
    if (width <= 0 || height <= 0) return

    for (const image of [this.backdropImage, this.sunMoonImage]) {
      image.setPosition(width / 2, height / 2)
      image.setScale(Math.max(width / image.width, height / image.height))
    }
    for (const child of this.cloud.getChildren()) {
      const cloud = child as Phaser.Physics.Arcade.Image
      cloud.setPosition(cloud.x * width / this.layoutWidth, cloud.y * height / this.layoutHeight)
    }
    this.layoutWidth = width
    this.layoutHeight = height
    this.physics.world.setBounds(0, 0, width, height)
  }

  update() {
    // Use wall time so sleep/resume and system clock changes do not drift the cycle.
    const now = Date.now()
    if (Math.abs(now - this.lastClockCheck) >= 1000) {
      this.lastClockCheck = now
      const mode = this.getTimeBackgroundMode()
      if (mode !== this.backgroundMode) {
        this.backgroundMode = mode
        this.setBackgroundTextures()
        this.backdropImage.setTexture(this.backdropKey)
        for (const child of this.cloud.getChildren()) {
          const cloud = child as Phaser.Physics.Arcade.Image
          cloud.setTexture(this.cloudKey, cloud.frame.name)
        }
        this.resizeBackground(this.scale.gameSize)
      }
    }
    this.physics.world.wrap(this.cloud, 500)
  }
}
