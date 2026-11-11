import Phaser from 'phaser'
import { PlayerBehavior } from '../types/PlayerBehavior'
import { DAILY_SCALE, hasDailyCharacterAssets } from './dailyCharacterAssets'
/**
 * shifting distance for sitting animation
 * format: direction: [xShift, yShift, depthShift]
 */
export const sittingShiftData = {
  up: [0, 3, -10],
  down: [0, 3, 1],
  left: [0, -8, 10],
  right: [0, -8, 10],
}

export default class Player extends Phaser.Physics.Arcade.Sprite {
  playerTexture: string
  playerBehavior = PlayerBehavior.IDLE
  playerName: Phaser.GameObjects.Text
  playerContainer: Phaser.GameObjects.Container
  private groundShadow: Phaser.GameObjects.Graphics

  constructor(
    scene: Phaser.Scene,
    x: number,
    y: number,
    texture: string,
    frame?: string | number
  ) {
    super(scene, x, y, texture, frame)

    this.playerTexture = texture
    this.setDepth(this.y)

    // The same flat pixel shadow is shared by standing, walking and reactions.
    this.groundShadow = scene.add.graphics().setName('character-ground-shadow')
    this.groundShadow.fillStyle(0x252831, 0.38)
      .fillRect(-9, -2, 18, 1)
      .fillRect(-12, -1, 24, 3)
      .fillRect(-9, 2, 18, 1)

    this.anims.play(`${this.playerTexture}_idle_down`, true)
    if (hasDailyCharacterAssets(texture)) this.setScale(DAILY_SCALE)

    this.playerContainer = this.scene.add.container(this.x, this.y - 30).setDepth(5000)


    // add playerName to playerContainer
    this.playerName = this.scene.add
      .text(0, 0, '')
      .setFontFamily('Arial')
      .setFontSize(12)
      .setColor('#000000')
      .setOrigin(0.5)
    this.playerContainer.add(this.playerName)

    this.scene.physics.world.enable(this.playerContainer)
    const playContainerBody = this.playerContainer.body as Phaser.Physics.Arcade.Body
    const collisionScale = [0.5, 0.2]
    playContainerBody
      .setSize(this.displayWidth * collisionScale[0], this.displayHeight * collisionScale[1])
      .setOffset(-8, this.displayHeight * (1 - collisionScale[1]) + 6)
    this.updateGroundShadow()
    // Arcade commits sprite positions in POST_UPDATE; follow after that commit.
    scene.events.on(Phaser.Scenes.Events.POST_UPDATE, this.updateGroundShadow, this)
  }

  protected get groundShadowVisible() {
    return this.active && this.visible && !this.anims.currentAnim?.key.includes('_sit_')
  }

  updateGroundShadow() {
    this.groundShadow.setPosition(this.x, this.y + 23)
      .setDepth(this.depth - 1).setVisible(this.groundShadowVisible).setAlpha(this.alpha)
  }

  destroy(fromScene?: boolean) {
    if (!this.scene) return
    this.scene.events.off(Phaser.Scenes.Events.POST_UPDATE, this.updateGroundShadow, this)
    // Phaser owns sibling display-list cleanup during scene shutdown.
    if (!fromScene) this.groundShadow.destroy()
    super.destroy(fromScene)
  }
}
