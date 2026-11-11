import Phaser from 'phaser'
import { employeePickupAssets, pickupAnimationKey, pickupTextureKey, type EmployeePickupCharacter } from '../anims/EmployeePickupAnims'
import { EmployeePressGesture } from './EmployeePressGesture'
import type { EmployeeInteraction } from './EmployeeInteraction'

export const PICKUP_PHRASES = [
  "ui.no_more_slacking_boss",
  "ui.put_me_down_boss",
  "ui.back_to_work_right_away",
  "ui.hey_my_collar_is_stretching",
  "ui.let_s_talk_this_through_boss",
  "ui.it_was_just_a_tiny_break",
  "ui.put_me_down_i_ll_focus",
  "ui.help_my_feet_can_t_reach_the_floor",
]

export type PickupMotion = {
  canLift: () => boolean
  move: (target: { x: number; y: number }) => void
}

const owners = new WeakMap<Phaser.Scene, EmployeePickup>()
const LIFT_HEIGHT = 14
const LIFT_DURATION = 160
const LAND_DURATION = 220

export class EmployeePickup {
  readonly gesture: EmployeePressGesture
  private sprite: Phaser.GameObjects.Sprite
  private effects: Phaser.GameObjects.Image
  private pointer?: Phaser.Input.Pointer
  private nativePointerId?: number
  private capturedPointerId?: number
  private pointerOffset = { x: 0, y: 0 }
  private elapsed = 0
  private landing = false
  private landingHeight = 0
  private liftHeight = 0
  private visible = false

  constructor(
    private host: Phaser.Physics.Arcade.Sprite,
    readonly character: EmployeePickupCharacter,
    private interaction: EmployeeInteraction,
    private motion: PickupMotion,
    private onLift: (phrase: string) => void,
    private onLand: () => void,
  ) {
    const scene = host.scene
    const asset = employeePickupAssets[character]
    this.sprite = scene.add.sprite(host.x, host.y, pickupTextureKey(character, 'body'), 0)
      .setOrigin(asset.anchorX / asset.frameWidth, asset.anchorY / asset.frameHeight)
      .setScale(asset.scale).setVisible(false)
    this.effects = scene.add.image(host.x, host.y, pickupTextureKey(character, 'effects'), 0)
      .setOrigin(this.sprite.originX, this.sprite.originY).setScale(asset.scale).setVisible(false)
    this.gesture = new EmployeePressGesture({
      schedule: (delay, callback) => {
        const timer = scene.time.delayedCall(delay, callback)
        return () => timer.remove(false)
      },
      lift: () => this.lift(),
      drop: () => {
        this.landing = true
        this.elapsed = 0
        this.landingHeight = this.liftHeight
      },
      click: () => interaction.click(),
    })
    scene.input.on('pointermove', this.movePointer)
    scene.input.on('pointerup', this.releasePointer)
    scene.input.on('pointerupoutside', this.releasePointer)
    const canvas = scene.game.canvas
    canvas.addEventListener('pointerdown', this.rememberNativePointer)
    canvas.addEventListener('pointerup', this.releaseNativePointer)
    canvas.addEventListener('pointercancel', this.cancelNativePointer)
    canvas.addEventListener('lostpointercapture', this.cancelNativePointer)
    window.addEventListener('blur', this.interrupt)
    document.addEventListener('visibilitychange', this.visibilityChanged)
  }

  private rememberNativePointer = (event: PointerEvent) => {
    if (event.button === 0 && !this.pointer) this.nativePointerId = event.pointerId
  }

  press(pointer: Phaser.Input.Pointer) {
    if (pointer.button !== 0 || this.pointer || this.visible || owners.has(this.host.scene)) return
    owners.set(this.host.scene, this)
    this.pointer = pointer
    this.interaction.enter()
    if (this.nativePointerId !== undefined) {
      this.host.scene.game.canvas.setPointerCapture(this.nativePointerId)
      this.capturedPointerId = this.nativePointerId
    }
    this.gesture.press(pointer)
  }

  private movePointer = (pointer: Phaser.Input.Pointer) => {
    this.gesture.move(pointer)
  }

  private contains(point: { x: number; y: number }) {
    const world = this.host.scene.cameras.main.getWorldPoint(point.x, point.y)
    return Math.abs(world.x - this.host.x) <= 20 && Math.abs(world.y - (this.host.y - 4)) <= 30
  }

  private releasePointer = (pointer: Phaser.Input.Pointer) => {
    if (pointer.button !== 0) return
    this.release(pointer)
  }

  private releaseNativePointer = (event: PointerEvent) => {
    if (event.pointerId !== this.capturedPointerId || event.button !== 0 || !this.pointer) return
    const scale = this.host.scene.scale
    this.release({ id: this.pointer.id, x: scale.transformX(event.clientX), y: scale.transformY(event.clientY) })
  }

  private release(point: { id: number; x: number; y: number }) {
    if (!this.pointer || point.id !== this.pointer.id) return
    if (this.gesture.dragging) this.moveGround(point)
    const inside = this.contains(point)
    if (inside) this.interaction.enter()
    else this.interaction.leave()
    this.gesture.release(point, inside)
    this.pointer = undefined
    this.releaseCapture()
    if (!this.visible) this.releaseOwner()
  }

  private cancelNativePointer = (event: PointerEvent) => {
    if (event.pointerId === this.capturedPointerId) this.interrupt()
  }

  private visibilityChanged = () => {
    if (document.hidden) this.interrupt()
  }

  private interrupt = () => {
    if (!this.pointer && !this.visible) return
    this.cancel()
    this.interaction.reset(true)
  }

  leaveCanvas() {
    if (this.gesture.dragging) return
    this.gesture.cancel()
    this.pointer = undefined
    this.releaseCapture()
    if (!this.visible) this.releaseOwner()
    this.interaction.leave()
  }

  private releaseCapture() {
    const pointerId = this.capturedPointerId
    this.capturedPointerId = undefined
    this.nativePointerId = undefined
    const canvas = this.host.scene.game.canvas
    if (pointerId !== undefined && canvas.hasPointerCapture(pointerId)) canvas.releasePointerCapture(pointerId)
  }

  private releaseOwner() {
    if (owners.get(this.host.scene) === this) owners.delete(this.host.scene)
  }

  private lift() {
    if (!this.pointer || !this.pointer.isDown || !this.motion.canLift() || !this.interaction.beginHold()) return false
    const world = this.host.scene.cameras.main.getWorldPoint(this.pointer.x, this.pointer.y)
    this.pointerOffset = { x: this.host.x - world.x, y: this.host.y - world.y }
    this.elapsed = 0
    this.landing = false
    this.liftHeight = 0
    this.visible = true
    this.sprite.setVisible(true).play(pickupAnimationKey(this.character))
    this.effects.setVisible(true)
    this.onLift(Phaser.Utils.Array.GetRandom(PICKUP_PHRASES))
    return true
  }

  private moveGround(point: { x: number; y: number }) {
    const world = this.host.scene.cameras.main.getWorldPoint(point.x, point.y)
    this.motion.move({ x: world.x + this.pointerOffset.x, y: world.y + this.pointerOffset.y })
  }

  get nameLift() {
    const asset = employeePickupAssets[this.character]
    const frame = asset.frames[Number(this.sprite.frame.name)]
    return Math.max(0, this.liftHeight + (asset.neutralTop - frame.contentTop) * asset.scale)
  }

  update(delta: number) {
    if (this.pointer && this.capturedPointerId !== undefined && !this.host.scene.game.canvas.hasPointerCapture(this.capturedPointerId)) {
      this.interrupt()
      return
    }
    if (!this.visible) return
    this.elapsed += delta
    if (this.landing) {
      const progress = Math.min(1, this.elapsed / LAND_DURATION)
      this.liftHeight = this.landingHeight * (1 - progress) ** 2
      if (progress === 1) {
        this.cancel()
        this.onLand()
        this.interaction.endHold()
        return
      }
    } else {
      this.liftHeight = LIFT_HEIGHT * (1 - (1 - Math.min(1, this.elapsed / LIFT_DURATION)) ** 3)
      if (this.pointer) this.moveGround(this.pointer)
    }
    this.sprite.setPosition(Math.round(this.host.x), Math.round(this.host.y + 24 - this.liftHeight)).setDepth(this.host.depth)
    this.effects.setPosition(this.sprite.x, this.sprite.y).setDepth(this.host.depth + 0.01).setFrame(this.sprite.frame.name)
  }

  cancel() {
    this.gesture.cancel()
    this.pointer = undefined
    this.releaseCapture()
    this.releaseOwner()
    this.visible = false
    this.landing = false
    this.liftHeight = 0
    this.sprite.stop().setVisible(false)
    this.effects.setVisible(false)
  }

  destroy(fromScene = false) {
    this.cancel()
    const scene = this.host.scene
    scene.input.off('pointermove', this.movePointer)
    scene.input.off('pointerup', this.releasePointer)
    scene.input.off('pointerupoutside', this.releasePointer)
    const canvas = scene.game.canvas
    canvas.removeEventListener('pointerdown', this.rememberNativePointer)
    canvas.removeEventListener('pointerup', this.releaseNativePointer)
    canvas.removeEventListener('pointercancel', this.cancelNativePointer)
    canvas.removeEventListener('lostpointercapture', this.cancelNativePointer)
    window.removeEventListener('blur', this.interrupt)
    document.removeEventListener('visibilitychange', this.visibilityChanged)
    if (!fromScene) { this.sprite.destroy(); this.effects.destroy() }
  }
}
