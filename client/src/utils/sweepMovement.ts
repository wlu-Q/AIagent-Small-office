export type Point = { x: number; y: number }
export type CollisionRect = Point & { width: number; height: number }
export type CollisionFootprint = { width: number; height: number; offsetX?: number; offsetY?: number }

export function sweepMovement(from: Point, target: Point, agent: CollisionFootprint, bounds: CollisionRect, obstacles: CollisionRect[]) {
  const halfWidth = agent.width / 2
  const halfHeight = agent.height / 2
  const offsetX = agent.offsetX ?? 0
  const offsetY = agent.offsetY ?? 0
  const limits = {
    left: bounds.x + halfWidth - offsetX,
    right: bounds.x + bounds.width - halfWidth - offsetX,
    top: bounds.y + halfHeight - offsetY,
    bottom: bounds.y + bounds.height - halfHeight - offsetY,
  }
  const position = { x: from.x, y: from.y }
  let remaining = { x: target.x - from.x, y: target.y - from.y }
  let collided = false
  for (let pass = 0; pass < 3 && Math.hypot(remaining.x, remaining.y) > 0.00001; pass++) {
    let time = 1
    let blockX = false
    let blockY = false
    const contact = (entry: number, horizontal: boolean, vertical: boolean) => {
      if (entry < -0.000001 || entry > time + 0.000001) return
      if (entry < time - 0.000001) { blockX = false; blockY = false }
      time = Math.max(0, Math.min(time, entry))
      blockX ||= horizontal
      blockY ||= vertical
    }
    if (remaining.x < 0) contact((limits.left - position.x) / remaining.x, true, false)
    if (remaining.x > 0) contact((limits.right - position.x) / remaining.x, true, false)
    if (remaining.y < 0) contact((limits.top - position.y) / remaining.y, false, true)
    if (remaining.y > 0) contact((limits.bottom - position.y) / remaining.y, false, true)
    for (const obstacle of obstacles) {
      const left = obstacle.x - halfWidth - offsetX
      const right = obstacle.x + obstacle.width + halfWidth - offsetX
      const top = obstacle.y - halfHeight - offsetY
      const bottom = obstacle.y + obstacle.height + halfHeight - offsetY
      if (remaining.x === 0 && (position.x <= left || position.x >= right)) continue
      if (remaining.y === 0 && (position.y <= top || position.y >= bottom)) continue
      const nearX = remaining.x === 0 ? -Infinity : Math.min((left - position.x) / remaining.x, (right - position.x) / remaining.x)
      const farX = remaining.x === 0 ? Infinity : Math.max((left - position.x) / remaining.x, (right - position.x) / remaining.x)
      const nearY = remaining.y === 0 ? -Infinity : Math.min((top - position.y) / remaining.y, (bottom - position.y) / remaining.y)
      const farY = remaining.y === 0 ? Infinity : Math.max((top - position.y) / remaining.y, (bottom - position.y) / remaining.y)
      const entry = Math.max(nearX, nearY)
      const exit = Math.min(farX, farY)
      if (entry > exit || exit <= 0) continue
      contact(entry, nearX >= nearY, nearY >= nearX)
    }
    const blocked = blockX || blockY
    const travel = blocked ? Math.max(0, time - 0.001 / Math.hypot(remaining.x, remaining.y)) : 1
    position.x += remaining.x * travel
    position.y += remaining.y * travel
    if (!blocked) break
    collided = true
    remaining = { x: blockX ? 0 : remaining.x * (1 - time), y: blockY ? 0 : remaining.y * (1 - time) }
  }
  return { ...position, collided }
}
