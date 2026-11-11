export const EMPLOYEE_LONG_PRESS_DELAY = 400
export const EMPLOYEE_PRESS_SLOP = 5

type PressPoint = { id: number; x: number; y: number }
type PressHooks = {
  schedule: (delay: number, callback: () => void) => () => void
  lift: () => boolean
  drop: () => void
  click: () => void
}

export class EmployeePressGesture {
  dragging = false
  private down?: PressPoint
  private clickable = false
  private cancelHold?: () => void

  constructor(private hooks: PressHooks) {}

  press(point: PressPoint) {
    if (this.down) return
    this.down = { id: point.id, x: point.x, y: point.y }
    this.clickable = true
    this.cancelHold = this.hooks.schedule(EMPLOYEE_LONG_PRESS_DELAY, () => {
      this.cancelHold = undefined
      this.clickable = false
      this.dragging = this.hooks.lift()
    })
  }

  move(point: PressPoint) {
    if (!this.down || point.id !== this.down.id || this.dragging) return
    if (Math.hypot(point.x - this.down.x, point.y - this.down.y) <= EMPLOYEE_PRESS_SLOP) return
    this.cancelHold?.()
    this.cancelHold = undefined
    this.clickable = false
  }

  release(point: PressPoint, inside: boolean) {
    if (!this.down || point.id !== this.down.id) return
    this.move(point)
    const wasDragging = this.dragging
    const shouldClick = this.clickable && inside
    this.cancel()
    if (wasDragging) this.hooks.drop()
    else if (shouldClick) this.hooks.click()
  }

  cancel() {
    this.cancelHold?.()
    this.cancelHold = undefined
    this.down = undefined
    this.clickable = false
    this.dragging = false
  }
}
