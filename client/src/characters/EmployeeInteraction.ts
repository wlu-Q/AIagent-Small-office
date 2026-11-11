export const HOVER_ACTIONS = ['nod', 'scratch', 'clap', 'stretch'] as const
export type EmployeeReactionAction = typeof HOVER_ACTIONS[number] | 'jump'
export const EMPLOYEE_LEAVE_DELAY = 1500
export const EMPLOYEE_DOUBLE_CLICK_DELAY = 300

type InteractionHooks = {
  schedule: (delay: number, callback: () => void) => () => void
  pause: () => void
  resume: () => void
  play: (action: EmployeeReactionAction) => void
  stand: () => void
  highlight: (visible: boolean) => void
  openChat: () => void
  random: () => number
}

// Only presentation state lives here. Office employee state remains authoritative.
export class EmployeeInteraction {
  active = false
  hovered = false
  jumping = false
  holding = false
  private enabled = false
  private cancelLeave?: () => void
  private cancelDoubleClickWindow?: () => void

  constructor(private hooks: InteractionHooks) {}

  setEnabled(enabled: boolean) {
    this.enabled = enabled
    if (!enabled) this.reset(false)
  }

  enter() {
    if (!this.enabled) return
    this.hovered = true
    this.cancelLeave?.()
    this.cancelLeave = undefined
    this.hooks.highlight(true)
    if (this.active) return
    this.active = true
    this.hooks.pause()
    this.hooks.play(HOVER_ACTIONS[Math.floor(this.hooks.random() * HOVER_ACTIONS.length)])
  }

  leave() {
    if (!this.active || !this.hovered) return
    this.hovered = false
    this.hooks.highlight(false)
    if (this.holding) return
    this.cancelLeave?.()
    this.cancelLeave = this.hooks.schedule(EMPLOYEE_LEAVE_DELAY, () => {
      this.cancelLeave = undefined
      this.reset(true)
    })
  }

  click() {
    if (!this.enabled || this.holding) return
    if (!this.active) this.enter()
    if (this.cancelDoubleClickWindow) {
      this.cancelDoubleClickWindow()
      this.cancelDoubleClickWindow = undefined
      this.jumping = false
      this.hooks.stand()
      this.hooks.openChat()
      // A DOM chat window can cover the canvas without another pointer move.
      this.leave()
      return
    }
    if (this.jumping) return
    this.jumping = true
    this.hooks.play('jump')
    // The first click reacts immediately. A second click opens chat without
    // restarting the hop; this window never delays the first visual response.
    this.cancelDoubleClickWindow = this.hooks.schedule(EMPLOYEE_DOUBLE_CLICK_DELAY, () => {
      this.cancelDoubleClickWindow = undefined
    })
  }

  animationComplete() {
    if (!this.active || this.holding) return
    this.jumping = false
    this.hooks.stand()
  }

  beginHold() {
    if (!this.enabled || this.holding) return false
    if (!this.active) this.enter()
    this.cancelLeave?.()
    this.cancelDoubleClickWindow?.()
    this.cancelLeave = undefined
    this.cancelDoubleClickWindow = undefined
    this.jumping = false
    this.holding = true
    return true
  }

  endHold() {
    if (!this.holding) return
    this.holding = false
    this.hooks.stand()
    if (!this.hovered) {
      this.hovered = true
      this.leave()
    }
  }

  reset(resume: boolean) {
    const wasActive = this.active
    this.cancelLeave?.()
    this.cancelDoubleClickWindow?.()
    this.cancelLeave = undefined
    this.cancelDoubleClickWindow = undefined
    this.active = false
    this.hovered = false
    this.jumping = false
    this.holding = false
    this.hooks.highlight(false)
    if (!wasActive) return
    this.hooks.stand()
    if (resume && this.enabled) this.hooks.resume()
  }
}
