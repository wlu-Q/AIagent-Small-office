import { t, i18n } from '../i18n'
import Phaser from 'phaser'

import Player, { sittingShiftData } from './Player'
import type { OfficeEmployeeSummary } from '../services/office/officeApi'
import type { EmployeeWorkstation } from '../scenes/employeeWorkstations'
import { PathfindingGrid, type NavigationAgent } from '../utils/pathfinding'
import { EmployeeReaction } from './EmployeeReaction'
import { hasEmployeeReactionAssets } from '../anims/EmployeeReactionAnims'
import { hasEmployeePickupAssets } from '../anims/EmployeePickupAnims'

export type IdleSeat = {
  id: string
  x: number
  y: number
  direction: 'up' | 'down' | 'left' | 'right'
}

const IDLE_SEAT_PROBABILITY = 0.3
const IDLE_MOVE_SPEED = 64
const WORK_MOVE_SPEED = 96
const NAVIGATION_GOAL_TOLERANCE = 24
const WORK_BUBBLE_OFFSET_Y = 47
const WORK_BUBBLE_TEXTS = [
  "ui.searching",
  "ui.hmm",
  "ui.reading_documents",
  "ui.oh",
  "ui.got_it",
  "ui.organizing",
  "ui.let_me_check",
  "ui.found_a_lead",
  "ui.working",
  "ui.this_should_work",
]

export default class OfficeEmployee extends Player {
  readonly employeeId: string
  readonly workstation: EmployeeWorkstation
  private projection: OfficeEmployeeSummary
  private navigation: PathfindingGrid
  private idleSeats: IdleSeat[]
  private occupiedIdleSeats: Set<string>
  private idleForbiddenAreas: Array<{ x: number; y: number; width: number; height: number }>
  private waypoints: Array<{ x: number; y: number }> = []
  private routeGoal?: { x: number; y: number }
  private routeTarget?: { x: number; y: number }
  private wanderTimer?: Phaser.Time.TimerEvent
  private homeRetryTimer?: Phaser.Time.TimerEvent
  private idleSeatTimer?: Phaser.Time.TimerEvent
  private workBubbleTimer?: Phaser.Time.TimerEvent
  private workBubbleHideTimer?: Phaser.Time.TimerEvent
  private workBubble: Phaser.GameObjects.Text
  private pendingIdleSeat?: IdleSeat
  private currentIdleSeat?: IdleSeat
  private recentWanderTargets: Array<{ x: number; y: number }> = []
  private movingHome = false
  private reaction?: EmployeeReaction

  private refreshLanguage = () => {
    const key = this.workBubble.getData('localeKey')
    if (key) this.workBubble.setText(t(key))
  }

  constructor(
    scene: Phaser.Scene,
    projection: OfficeEmployeeSummary,
    workstation: EmployeeWorkstation,
    texture: string,
    navigation: PathfindingGrid,
    idleSeats: IdleSeat[] = [],
    occupiedIdleSeats = new Set<string>(),
    idleForbiddenAreas: Array<{ x: number; y: number; width: number; height: number }> = [],
  ) {
    super(scene, workstation.x, workstation.y, texture)
    this.employeeId = projection.id
    this.workstation = workstation
    this.projection = projection
    this.navigation = navigation
    this.idleSeats = idleSeats
    this.occupiedIdleSeats = occupiedIdleSeats
    this.idleForbiddenAreas = idleForbiddenAreas
    scene.add.existing(this)
    scene.physics.add.existing(this)
    const body = this.body as Phaser.Physics.Arcade.Body
    const collisionScale = [0.5, 0.2]
    body
      .setSize(this.width * collisionScale[0], this.height * collisionScale[1])
      .setOffset(
        this.width * (1 - collisionScale[0]) * 0.5,
        this.height * (1 - collisionScale[1])
      )
      .setCollideWorldBounds(true)

    i18n.on('languageChanged', this.refreshLanguage)
    this.workBubble = scene.add
      .text(this.x, this.y - WORK_BUBBLE_OFFSET_Y, '', {
        color: '#334155',
        fontFamily: 'Arial',
        fontSize: '11px',
        backgroundColor: '#ffffff',
        padding: { left: 6, right: 6, top: 4, bottom: 4 },
        stroke: '#cbd5e1',
        strokeThickness: 1,
      })
      .setOrigin(0.5, 1)
      .setDepth(7000)
      .setAlpha(0)
      .setVisible(false)

    if (hasEmployeeReactionAssets(texture)) {
      this.reaction = new EmployeeReaction(this, texture, () => this.pauseForReaction(), () => this.resumeAfterReaction(),
        hasEmployeePickupAssets(texture) ? {
          canLift: () => this.navigation.canDragFrom(this, this.getNavigationAgent()),
          move: target => {
            const position = this.navigation.constrainDrag(this, target, this.getNavigationAgent())
            body.reset(position.x, position.y)
            this.setDepth(position.y)
          },
        } : undefined,
      )
    }
    this.applyProjection(projection)
  }

  private pauseForReaction() {
    this.cancelWander()
    this.leaveIdleSeat(false)
    this.stopRoute()
    this.anims.stop()
    this.setDepth(this.y)
    this.playerContainer.setPosition(this.x, this.y - 30)
  }

  private resumeAfterReaction() {
    if (this.projection.state !== 'idle') return
    this.playIdleAnimation()
    this.beginIdleWander()
  }

  protected get groundShadowVisible() {
    return this.reaction?.interaction.active || super.groundShadowVisible
  }

  applyProjection(projection: OfficeEmployeeSummary) {
    const previousState = this.projection.state
    this.projection = projection
    this.reaction?.setEnabled(projection.state === 'idle')
    this.playerName.setText(projection.displayName)
    this.setTint(projection.state === 'disabled' ? 0x94a3b8 : 0xffffff)

    if (projection.state === 'working') {
      this.cancelWander()
      this.leaveIdleSeat(false)
      this.scheduleWorkBubble()
      // A repeated working projection must not restart an in-flight route.
      // Only a real transition, a missing route, or a non-home position should
      // schedule a return to the employee's assigned workstation.
      if (!this.movingHome && this.isAtWorkstation()) {
        this.cancelHomeRetry()
        this.stopRoute()
        this.movingHome = false
        this.playWorkAnimation()
      } else if (
        previousState !== 'working' ||
        (!this.movingHome && this.waypoints.length === 0 && !this.homeRetryTimer)
      ) {
        this.moveHome()
      }
      return
    }

    this.clearWorkBubble()

    if (projection.state === 'idle') {
      this.cancelHomeRetry()
      if (previousState !== 'idle') {
        this.leaveIdleSeat(false)
        this.stopRoute()
        this.movingHome = false
        this.playIdleAnimation()
      }
      this.scheduleWander()
      return
    }

    this.cancelHomeRetry()
    // Disabled employees stay where they are and do not start wandering.
    // This is intentionally different from working: disabled is not a
    // command to return to a workstation.
    this.cancelWander()
    this.leaveIdleSeat(false)
    if (previousState !== 'disabled') {
      this.stopRoute()
      this.movingHome = false
      this.playIdleAnimation()
    }
  }

  private scheduleWander() {
    if (
      this.wanderTimer ||
      this.idleSeatTimer ||
      this.pendingIdleSeat ||
      this.currentIdleSeat ||
      this.waypoints.length > 0 ||
      this.reaction?.interaction.active ||
      this.projection.state !== 'idle'
    ) return
    const delay = Phaser.Math.Between(1800, 4200)
    this.wanderTimer = this.scene.time.delayedCall(delay, () => {
      this.wanderTimer = undefined
      this.beginIdleWander()
    })
  }

  private chooseWanderTarget() {
    const agent = this.getNavigationAgent()
    const minimumDistance = 96

    // Pick from the whole navigation grid instead of the employee's local
    // neighbourhood. Route validation below still prevents crossing walls or
    // furniture, while allowing idle employees to reach the right-side rooms.
    for (let attempt = 0; attempt < 96; attempt += 1) {
      const candidate = this.navigation.randomWalkableWorld(agent)
      if (!candidate) return undefined

      const distance = Math.hypot(candidate.x - this.x, candidate.y - this.y)
      if (distance < minimumDistance) continue
      if (this.idleForbiddenAreas.some((area) => (
        candidate.x >= area.x &&
        candidate.x <= area.x + area.width &&
        candidate.y >= area.y &&
        candidate.y <= area.y + area.height
      ))) continue
      if (this.recentWanderTargets.some((target) =>
        Math.hypot(candidate.x - target.x, candidate.y - target.y) < 64
      )) {
        continue
      }

      const route = this.navigation.findRoute(
        { x: this.x, y: this.y },
        candidate,
        agent,
      )
      if (route.waypoints.length === 0) continue

      return candidate
    }

    return undefined
  }

  private chooseIdleSeat() {
    const availableSeats = this.idleSeats
      .filter((seat) => !this.occupiedIdleSeats.has(seat.id))
      .sort(() => Math.random() - 0.5)
    const agent = this.getNavigationAgent()

    for (const seat of availableSeats) {
      const distance = Math.hypot(seat.x - this.x, seat.y - this.y)
      if (distance < 64) continue
      const route = this.navigation.findRoute(
        { x: this.x, y: this.y },
        { x: seat.x, y: seat.y },
        agent,
      )
      if (route.waypoints.length > 0) return seat
    }

    return undefined
  }

  private rememberWanderTarget(target: { x: number; y: number }) {
    this.recentWanderTargets = [
      ...this.recentWanderTargets,
      { x: target.x, y: target.y },
    ].slice(-4)
  }

  beginIdleWander() {
    if (this.projection.state !== 'idle' || this.reaction?.interaction.active) return
    this.cancelWander()

    if (Math.random() < IDLE_SEAT_PROBABILITY) {
      const seat = this.chooseIdleSeat()
      if (seat) {
        this.occupiedIdleSeats.add(seat.id)
        this.pendingIdleSeat = seat
        if (this.setRoute({ x: seat.x, y: seat.y })) {
          this.rememberWanderTarget(seat)
          return
        }
        this.releaseIdleSeat()
      }
    }

    const target = this.chooseWanderTarget()
    if (target && this.setRoute(target)) {
      this.rememberWanderTarget(target)
      return
    }
    this.scheduleWander()
  }

  private cancelWander() {
    this.wanderTimer?.remove(false)
    this.wanderTimer = undefined
  }

  private scheduleHomeRetry() {
    if (this.homeRetryTimer || this.projection.state !== 'working') return
    this.homeRetryTimer = this.scene.time.delayedCall(1200, () => {
      this.homeRetryTimer = undefined
      if (this.projection.state === 'working' && !this.isAtWorkstation()) {
        this.moveHome()
      }
    })
  }

  private cancelHomeRetry() {
    this.homeRetryTimer?.remove(false)
    this.homeRetryTimer = undefined
  }

  private scheduleIdleSeatRelease() {
    this.idleSeatTimer?.remove(false)
    this.idleSeatTimer = this.scene.time.delayedCall(Phaser.Math.Between(5000, 12000), () => {
      this.idleSeatTimer = undefined
      if (this.projection.state === 'idle') this.leaveIdleSeat()
    })
  }

  private releaseIdleSeat() {
    const seat = this.currentIdleSeat || this.pendingIdleSeat
    if (seat) this.occupiedIdleSeats.delete(seat.id)
    this.pendingIdleSeat = undefined
    this.currentIdleSeat = undefined
  }

  private leaveIdleSeat(scheduleNext = true) {
    this.idleSeatTimer?.remove(false)
    this.idleSeatTimer = undefined
    const seat = this.currentIdleSeat
    this.releaseIdleSeat()
    if (seat) {
      this.setPosition(seat.x, seat.y)
      this.setDepth(this.y)
      this.playerContainer.setPosition(this.x, this.y - 30)
    }
    if (scheduleNext && this.projection.state === 'idle') {
      this.playIdleAnimation()
      this.scheduleWander()
    }
  }

  private moveHome() {
    this.cancelWander()
    this.cancelHomeRetry()
    this.leaveIdleSeat(false)
    if (this.isAtWorkstation()) {
      this.stopRoute()
      this.movingHome = false
      this.playWorkAnimation()
      return
    }

    this.movingHome = true
    if (!this.setRoute({ x: this.workstation.x, y: this.workstation.y })) {
      // Never teleport when the workstation route is temporarily unavailable.
      // Keep the employee in place and allow a later projection to retry.
      this.movingHome = false
      this.stopRoute()
      this.playIdleAnimation()
      this.scheduleHomeRetry()
    }
  }

  private isAtWorkstation() {
    return Math.hypot(this.workstation.x - this.x, this.workstation.y - this.y) <= 8
  }

  private canCommitArrival(
    point: { x: number; y: number },
    target: { x: number; y: number },
  ) {
    const closeEnough = Math.hypot(target.x - point.x, target.y - point.y) <= NAVIGATION_GOAL_TOLERANCE
    if (!closeEnough) return false
    return this.navigation.isWorldPositionWalkable(target, this.getNavigationAgent())
  }

  private stopRoute() {
    this.setVelocity(0, 0)
    ;(this.body as Phaser.Physics.Arcade.Body).stop()
    this.waypoints = []
    this.routeGoal = undefined
    this.routeTarget = undefined
  }

  private playSeatAnimation(seat: IdleSeat) {
    this.anims.timeScale = 1
    this.anims.play(`${this.playerTexture}_sit_${seat.direction}`, true)
    const [xShift, yShift, depthShift] = sittingShiftData[seat.direction]
    this.setPosition(seat.x + xShift, seat.y + yShift)
    this.setDepth(seat.y + depthShift)
    this.playerContainer.setPosition(this.x, this.y - 30)
  }

  private playWorkAnimation() {
    this.anims.timeScale = 1
    // The six workstations in the left expansion are the lower row of the
    // copied desk layout. Their screen is above the chair, so employees must
    // use the back-facing sit_up pose. Keep this override scoped to workstation
    // 01-06; the existing right-side seat behavior remains untouched.
    const isLeftExpansionWorkstation = /^workstation-0[1-6]$/.test(this.workstation.id)
    const direction = isLeftExpansionWorkstation ? 'up' : this.workstation.direction
    this.anims.play(`${this.playerTexture}_sit_${direction}`, true)

    if (isLeftExpansionWorkstation) {
      const [xShift, yShift, depthShift] = sittingShiftData[direction]
      this.setPosition(this.workstation.x + xShift, this.workstation.y + yShift)
      this.setDepth(this.workstation.y + depthShift)
      this.playerContainer.setPosition(this.x, this.y - 30)
    }
    this.updateWorkBubblePosition()
  }

  private playIdleAnimation() {
    this.anims.timeScale = 1
    this.anims.play(`${this.playerTexture}_idle_down`, true)
    this.setDepth(this.y)
    this.playerContainer.setPosition(this.x, this.y - 30)
  }

  private getNavigationAgent(): NavigationAgent & { offsetY: number } {
    return {
      width: this.displayWidth * 0.5,
      height: this.displayHeight * 0.2,
      offsetY: this.displayHeight * 0.4,
    }
  }

  private setRoute(target: { x: number; y: number }) {
    this.stopRoute()
    const route = this.navigation.findRoute(
      { x: this.x, y: this.y },
      target,
      this.getNavigationAgent(),
    )
    this.waypoints = route.waypoints
    this.routeGoal = this.waypoints.length > 0 ? route.resolvedGoal : undefined
    if (this.routeGoal && this.navigation.canTraverseWorld(this.routeGoal, target, this.getNavigationAgent())) {
      if (Math.hypot(this.routeGoal.x - target.x, this.routeGoal.y - target.y) >= 0.05) {
        this.waypoints.push({ ...target })
      }
      this.routeGoal = { ...target }
    }
    this.routeTarget = this.waypoints.length > 0 ? { ...target } : undefined
    return this.waypoints.length > 0
  }

  private finishRoute(point?: { x: number; y: number }) {
    const returningHome = this.movingHome
    const arrivingSeat = !returningHome ? this.pendingIdleSeat : undefined
    this.stopRoute()
    if (point) this.setPosition(point.x, point.y)

    if (returningHome) {
      const arrivedAtWorkstation = point !== undefined && this.canCommitArrival(
        point,
        { x: this.workstation.x, y: this.workstation.y },
      )
      this.movingHome = false
      if (arrivedAtWorkstation) {
        this.cancelHomeRetry()
        this.setPosition(this.workstation.x, this.workstation.y)
        if (this.projection.state === 'working') this.playWorkAnimation()
        else this.playIdleAnimation()
      } else {
        this.playIdleAnimation()
        this.scheduleHomeRetry()
      }
    } else if (arrivingSeat) {
      this.pendingIdleSeat = undefined
      const arrivedAtSeat = point !== undefined && this.canCommitArrival(
        point,
        { x: arrivingSeat.x, y: arrivingSeat.y },
      )
      if (arrivedAtSeat && this.projection.state === 'idle') {
        this.currentIdleSeat = arrivingSeat
        this.setPosition(arrivingSeat.x, arrivingSeat.y)
        this.playSeatAnimation(arrivingSeat)
        this.scheduleIdleSeatRelease()
      } else {
        this.occupiedIdleSeats.delete(arrivingSeat.id)
        this.playIdleAnimation()
        this.scheduleWander()
      }
    } else {
      this.playIdleAnimation()
      this.scheduleWander()
    }
    this.setDepth(this.currentIdleSeat ? this.currentIdleSeat.y + sittingShiftData[this.currentIdleSeat.direction][2] : this.y)
    this.playerContainer.setPosition(this.x, this.y - 30)
    this.updateWorkBubblePosition()
  }

  updateOffice(delta: number) {
    this.updateWorkBubblePosition()
    this.reaction?.update(delta)
    if (this.reaction?.interaction.active) {
      this.playerContainer.setPosition(this.x, this.y - 30 - this.reaction.nameLift)
      return
    }
    if (!this.routeGoal) return

    const body = this.body as Phaser.Physics.Arcade.Body
    const navigationAgent = this.getNavigationAgent()
    const position = { x: body.center.x, y: body.center.y - navigationAgent.offsetY }
    while (this.waypoints.length > 0 && Math.hypot(
      this.waypoints[0].x - position.x, this.waypoints[0].y - position.y,
    ) < 0.05) this.waypoints.shift()
    if (this.waypoints.length === 0) {
      body.reset(position.x, position.y)
      this.finishRoute(this.routeGoal)
      return
    }

    const target = this.waypoints[0]
    const dx = target.x - position.x
    const dy = target.y - position.y
    const distance = Math.hypot(dx, dy)
    if (!this.navigation.canTraverseWorld(
      position,
      target,
      navigationAgent,
    )) {
      const routeTarget = this.routeTarget
      if (routeTarget && this.setRoute(routeTarget)) return
      this.finishRoute()
      return
    }

    const moveSpeed = this.projection.state === 'idle' ? IDLE_MOVE_SPEED : WORK_MOVE_SPEED
    const physicsWorld = this.scene.physics.world
    const stepSeconds = physicsWorld.fixedStep ? 1 / physicsWorld.fps : Math.max(delta, 1) / 1000
    const speed = Math.min(moveSpeed, distance / stepSeconds)
    const angle = Math.atan2(dy, dx)
    const velocityX = Math.cos(angle) * speed
    const velocityY = Math.sin(angle) * speed
    this.setVelocity(velocityX, velocityY)
    this.setDepth(position.y)
    this.playerContainer.setPosition(position.x, position.y - 30)
    this.anims.timeScale = moveSpeed / IDLE_MOVE_SPEED

    if (Math.abs(velocityX) > Math.abs(velocityY)) {
      this.anims.play(
        `${this.playerTexture}_run_${velocityX >= 0 ? 'right' : 'left'}`,
        true
      )
      return
    }

    this.anims.play(
      `${this.playerTexture}_run_${velocityY >= 0 ? 'down' : 'up'}`,
      true
    )
  }

  private scheduleWorkBubble() {
    if (this.workBubbleTimer || this.projection.state !== 'working') return
    const delay = this.workBubble.visible ? Phaser.Math.Between(2800, 5000) : Phaser.Math.Between(700, 1600)
    this.workBubbleTimer = this.scene.time.delayedCall(delay, () => {
      this.workBubbleTimer = undefined
      if (this.projection.state !== 'working') return
      const phrase = Phaser.Utils.Array.GetRandom(WORK_BUBBLE_TEXTS)
      this.workBubble.setData('localeKey', phrase).setText(t(phrase))
      this.workBubble.setPosition(this.x, this.y - WORK_BUBBLE_OFFSET_Y)
      this.workBubble.setVisible(true).setAlpha(0)
      this.scene.tweens.add({ targets: this.workBubble, alpha: 1, duration: 160 })
      this.workBubbleHideTimer?.remove(false)
      this.workBubbleHideTimer = this.scene.time.delayedCall(1500, () => {
        this.workBubbleHideTimer = undefined
        this.scene.tweens.add({
          targets: this.workBubble,
          alpha: 0,
          duration: 180,
          onComplete: () => this.workBubble.setVisible(false),
        })
      })
      this.scheduleWorkBubble()
    })
  }

  private clearWorkBubble() {
    this.workBubbleTimer?.remove(false)
    this.workBubbleTimer = undefined
    this.workBubbleHideTimer?.remove(false)
    this.workBubbleHideTimer = undefined
    this.scene.tweens.killTweensOf(this.workBubble)
    this.workBubble.setVisible(false).setAlpha(0)
  }

  private updateWorkBubblePosition() {
    if (this.workBubble.visible) this.workBubble.setPosition(this.x, this.y - WORK_BUBBLE_OFFSET_Y)
  }

  getProjection() {
    return this.projection
  }

  destroy(fromScene?: boolean) {
    i18n.off('languageChanged', this.refreshLanguage)
    if (!this.scene) return
    this.reaction?.destroy(fromScene)
    this.reaction = undefined
    this.cancelWander()
    this.cancelHomeRetry()
    this.idleSeatTimer?.remove(false)
    this.clearWorkBubble()
    this.releaseIdleSeat()
    this.stopRoute()
    if (!fromScene) {
      this.playerContainer.destroy(true)
      this.workBubble.destroy()
    }
    super.destroy(fromScene)
  }
}
