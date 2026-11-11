import { t, i18n } from '../i18n'
import Phaser from 'phaser'
import {
  employeeReactionAssets, reactionAnimationKey, reactionTextureKey, reactionFrameKey,
  type EmployeeReactionCharacter,
} from '../anims/EmployeeReactionAnims'
import { EmployeeInteraction, type EmployeeReactionAction } from './EmployeeInteraction'
import { openEmployeeChat } from '../services/office/employeeChat'
import { EmployeePickup, type PickupMotion } from './EmployeePickup'
import { hasEmployeePickupAssets } from '../anims/EmployeePickupAnims'

const greetings: Record<EmployeeReactionAction, string[]> = {
  nod: ["ui.hi_boss", "ui.what_can_i_do_for_you_boss"],
  scratch: ["ui.oh_hi_boss", "ui.looking_for_me_boss"],
  clap: ["ui.hello_boss", "ui.let_s_do_our_best_today"],
  stretch: ["ui.a_quick_stretch_to_recharge", "ui.hi_boss_ready_to_go"],
  jump: ["ui.whoa", "ui.you_startled_me", "ui.right_here_boss"],
}

type ReactionHost = Phaser.Physics.Arcade.Sprite & { employeeId: string }

/** Presentation is independent of the host's physics body and navigation anchor. */
export class EmployeeReaction {
  readonly interaction: EmployeeInteraction
  private hitArea: Phaser.GameObjects.Zone
  private sprite: Phaser.GameObjects.Sprite
  private outline: Phaser.GameObjects.Sprite[]
  private action: EmployeeReactionAction = 'nod'
  private bubble: Phaser.GameObjects.Text
  private refreshLanguage = () => {
    const key = this.bubble.getData('localeKey')
    if (key) this.bubble.setText(t(key))
  }
  private bubbleTimer?: Phaser.Time.TimerEvent
  private highlighted = false
  private down?: { id: number; x: number; y: number }
  private pickup?: EmployeePickup
  private leaveCanvas = () => {
    this.down = undefined
    if (this.pickup) this.pickup.leaveCanvas()
    else this.interaction.leave()
  }

  constructor(private host: ReactionHost, readonly character: EmployeeReactionCharacter, pause: () => void, resume: () => void, pickupMotion?: PickupMotion) {
    const scene = host.scene
    i18n.on('languageChanged', this.refreshLanguage)
    const texture = reactionTextureKey(character, 'nod')
    this.sprite = scene.add.sprite(host.x, host.y + 24, texture, reactionFrameKey('nod', 0)).setVisible(false)
    this.outline = [[-1, 0], [1, 0], [0, -1], [0, 1]].map(([x, y]) => (
      scene.add.sprite(x, y, texture, reactionFrameKey('nod', 0))
        .setTintFill(0x9cefff).setVisible(false)
        .setData('offset', { x, y })
    ))
    this.applyAsset('nod')
    this.bubble = scene.add.text(host.x, host.y - 48, '', {
      color: '#334155', backgroundColor: '#ffffff', fontFamily: 'Arial', fontSize: '11px',
      padding: { left: 7, right: 7, top: 5, bottom: 5 },
      stroke: '#cbd5e1', strokeThickness: 1,
    }).setOrigin(0.5, 1).setDepth(7000).setVisible(false)
    // Stable ground-relative bounds do not follow the arms or jumping image.
    this.hitArea = scene.add.zone(host.x, host.y - 4, 40, 60).setName(`employee:${host.employeeId}`)
      .setInteractive({ useHandCursor: true })
    this.interaction = new EmployeeInteraction({
      schedule: (delay, callback) => {
        const timer = scene.time.delayedCall(delay, callback)
        return () => timer.remove(false)
      },
      pause: () => { pause(); host.setVisible(false); this.sprite.setVisible(true) },
      resume: () => { this.hide(); resume() },
      play: action => this.play(action),
      stand: () => this.stand(),
      highlight: visible => { this.highlighted = visible },
      openChat: () => openEmployeeChat(host.employeeId),
      random: Math.random,
    })
    if (pickupMotion && hasEmployeePickupAssets(character)) {
      this.pickup = new EmployeePickup(host, character, this.interaction, pickupMotion, phrase => {
        this.sprite.stop().setVisible(false)
        this.outline.forEach(sprite => sprite.setVisible(false))
        this.showBubble(phrase, 2600)
      }, () => {
        this.applyAsset('nod')
        this.sprite.setVisible(true)
        this.bubble.setVisible(false)
      })
    }
    this.sprite.on(Phaser.Animations.Events.ANIMATION_UPDATE, () => this.applyFrame())
    this.sprite.on(Phaser.Animations.Events.ANIMATION_COMPLETE, () => this.interaction.animationComplete())
    this.hitArea.on('pointerover', () => this.interaction.enter())
    this.hitArea.on('pointerout', () => { this.down = undefined; this.interaction.leave() })
    this.hitArea.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      if (this.pickup) { this.pickup.press(pointer); return }
      if (pointer.button === 0) this.down = { id: pointer.id, x: pointer.x, y: pointer.y }
    })
    this.hitArea.on('pointerup', (pointer: Phaser.Input.Pointer) => {
      if (this.pickup) return
      const down = this.down
      this.down = undefined
      if (pointer.button !== 0 || !down || down.id !== pointer.id) return
      if (Math.hypot(pointer.x - down.x, pointer.y - down.y) > 5) return
      this.interaction.click()
    })
    scene.game.canvas.addEventListener('pointerleave', this.leaveCanvas)
    window.addEventListener('blur', this.leaveCanvas)
  }

  setEnabled(enabled: boolean) {
    if (!enabled) this.pickup?.cancel()
    this.interaction.setEnabled(enabled)
    if (this.hitArea.input) this.hitArea.input.enabled = enabled
    if (!enabled) { this.down = undefined; this.hide() }
  }

  private play(action: EmployeeReactionAction) {
    this.sprite.anims.stop()
    this.applyAsset(action)
    this.sprite.play(reactionAnimationKey(this.character, action))
    this.applyFrame()
    const duration = employeeReactionAssets[this.character][action].steps.reduce((total, step) => total + step.duration, 0)
    this.showBubble(Phaser.Utils.Array.GetRandom(greetings[action]), Math.max(1200, duration))
  }

  private showBubble(key: string, duration: number) {
    this.bubble.setData('localeKey', key).setText(t(key)).setVisible(true)
    this.bubbleTimer?.remove(false)
    this.bubbleTimer = this.host.scene.time.delayedCall(duration, () => {
      this.bubbleTimer = undefined
      this.bubble.setVisible(false)
    })
  }

  private applyAsset(action: EmployeeReactionAction) {
    this.action = action
    const asset = employeeReactionAssets[this.character][action]
    for (const sprite of [this.sprite, ...this.outline]) {
      sprite.setTexture(reactionTextureKey(this.character, action), reactionFrameKey(action, 0))
        .setScale(asset.scale)
    }
    this.applyFrame()
  }

  get frameIndex() {
    return Number(this.sprite.frame.name.slice(this.action.length + 1))
  }

  private applyFrame() {
    const frame = employeeReactionAssets[this.character][this.action].frames[this.frameIndex]
    const [, , width, height] = frame.source
    for (const sprite of [this.sprite, ...this.outline]) {
      sprite.setFrame(this.sprite.frame.name).setOrigin(frame.anchorX / width, frame.anchorY / height)
    }
    // Only assets containing a baked ground shadow are clipped. Jump anchors
    // can lie below the frame, so anchorY is not a generic cropping boundary.
    if (frame.visibleHeight < height) this.sprite.setCrop(0, 0, width, frame.visibleHeight)
    else this.sprite.setCrop()
  }

  private stand() {
    if (!this.sprite.scene) return
    this.sprite.anims.stop()
    this.sprite.setFrame(reactionFrameKey(this.action, 0))
    this.applyFrame()
  }

  private hide() {
    this.stand()
    this.sprite.setVisible(false)
    this.outline.forEach(sprite => sprite.setVisible(false))
    this.bubbleTimer?.remove(false)
    this.bubbleTimer = undefined
    this.bubble.setVisible(false)
    this.host.setVisible(true)
  }

  get nameLift() {
    if (this.interaction.holding && this.pickup) return this.pickup.nameLift
    const asset = employeeReactionAssets[this.character][this.action]
    const neutral = asset.frames[0], frame = asset.frames[this.frameIndex]
    // Keep the normal label gap; lift it only for raised poses and GIF emotes.
    return Math.max(0, (neutral.contentTop - neutral.anchorY - frame.contentTop + frame.anchorY) * asset.scale)
  }

  update(delta: number) {
    this.pickup?.update(delta)
    const { x, y, depth } = this.host
    this.hitArea.setPosition(x, y - 4).setDepth(depth)
    if (!this.interaction.active) return
    this.sprite.setPosition(x, y + 24).setDepth(depth)
    this.bubble.setPosition(x, y - 62 - (this.interaction.holding ? this.nameLift : 0))
    if (this.interaction.holding) return
    const bounds = employeeReactionAssets[this.character][this.action].frames[this.frameIndex].bodyBounds
    this.outline.forEach(sprite => {
      const offset = sprite.getData('offset') as { x: number; y: number }
      sprite.setPosition(x + offset.x, y + 24 + offset.y).setDepth(depth - 0.1)
        .setFrame(this.sprite.frame.name).setCrop(bounds[0], bounds[1], bounds[2], bounds[3])
        .setVisible(this.interaction.active && this.highlighted)
    })
  }

  destroy(fromScene = false) {
    i18n.off('languageChanged', this.refreshLanguage)
    this.pickup?.destroy(fromScene)
    this.interaction.setEnabled(false)
    this.bubbleTimer?.remove(false)
    this.host.scene.game.canvas.removeEventListener('pointerleave', this.leaveCanvas)
    window.removeEventListener('blur', this.leaveCanvas)
    // During scene shutdown Phaser iterates the display list itself. Removing
    // sibling entries here would invalidate that iteration's current index.
    if (!fromScene) {
      this.hitArea.destroy()
      this.sprite.destroy()
      this.bubble.destroy()
      this.outline.forEach(sprite => sprite.destroy())
    }
  }
}
