import { sweepMovement } from './sweepMovement'

type Cell = {
  x: number
  y: number
}

type Rect = {
  x: number
  y: number
  width: number
  height: number
}

export type NavigationAgent = {
  width: number
  height: number
  offsetX?: number
  offsetY?: number
}

export type NavigationConfig = {
  width: number
  height: number
  tileWidth: number
  tileHeight: number
  blockedRects: Rect[]
  obstaclePadding?: number
  originX?: number
  originY?: number
}

function inflateRect(rect: Rect, padding: number): Rect {
  return {
    x: rect.x - padding,
    y: rect.y - padding,
    width: rect.width + padding * 2,
    height: rect.height + padding * 2,
  }
}

function toCellKey(cell: Cell) {
  return `${cell.x},${cell.y}`
}

function manhattan(a: Cell, b: Cell) {
  return Math.abs(a.x - b.x) + Math.abs(a.y - b.y)
}

function rectanglesOverlap(a: Rect, b: Rect) {
  return !(
    a.x + a.width <= b.x ||
    a.x >= b.x + b.width ||
    a.y + a.height <= b.y ||
    a.y >= b.y + b.height
  )
}

export class PathfindingGrid {
  private width: number
  private height: number
  private tileWidth: number
  private tileHeight: number
  private originX: number
  private originY: number
  private blocked: boolean[][]
  private blockedRects: Rect[]

  constructor(config: NavigationConfig) {
    this.width = config.width
    this.height = config.height
    this.tileWidth = config.tileWidth
    this.tileHeight = config.tileHeight
    this.originX = config.originX ?? 0
    this.originY = config.originY ?? 0
    this.blocked = Array.from({ length: this.height }, () => Array(this.width).fill(false))

    const obstaclePadding = config.obstaclePadding ?? 0
    this.blockedRects = config.blockedRects.map((rect) => inflateRect(rect, obstaclePadding))

    this.blockedRects.forEach((rect) => {
      const startX = Math.max(0, Math.floor((rect.x - this.originX) / this.tileWidth))
      const endX = Math.min(
        this.width - 1,
        Math.floor((rect.x + rect.width - 1 - this.originX) / this.tileWidth)
      )
      const startY = Math.max(0, Math.floor((rect.y - this.originY) / this.tileHeight))
      const endY = Math.min(
        this.height - 1,
        Math.floor((rect.y + rect.height - 1 - this.originY) / this.tileHeight)
      )

      for (let y = startY; y <= endY; y += 1) {
        for (let x = startX; x <= endX; x += 1) {
          this.blocked[y][x] = true
        }
      }
    })
  }

  worldToCell(x: number, y: number): Cell {
    return {
      x: Math.max(0, Math.min(this.width - 1, Math.floor((x - this.originX) / this.tileWidth))),
      y: Math.max(0, Math.min(this.height - 1, Math.floor((y - this.originY) / this.tileHeight))),
    }
  }

  cellToWorld(cell: Cell) {
    return {
      x: this.originX + cell.x * this.tileWidth + this.tileWidth / 2,
      y: this.originY + cell.y * this.tileHeight + this.tileHeight / 2,
    }
  }

  isBlocked(cell: Cell) {
    return this.blocked[cell.y]?.[cell.x] ?? true
  }

  isWorldPositionWalkable(world: { x: number; y: number }, agent?: NavigationAgent) {
    if (!agent) return !this.isBlocked(this.worldToCell(world.x, world.y))

    const footprint: Rect = {
      x: world.x + (agent.offsetX ?? 0) - agent.width / 2,
      y: world.y + (agent.offsetY ?? 0) - agent.height / 2,
      width: agent.width,
      height: agent.height,
    }
    return !this.blockedRects.some((rect) => rectanglesOverlap(footprint, rect))
  }

  canTraverseWorld(
    from: { x: number; y: number },
    to: { x: number; y: number },
    agent?: NavigationAgent,
    sampleDistance = 4,
  ) {
    const distance = Math.hypot(to.x - from.x, to.y - from.y)
    const steps = Math.max(1, Math.ceil(distance / sampleDistance))
    for (let index = 0; index <= steps; index += 1) {
      const progress = index / steps
      const point = {
        x: from.x + (to.x - from.x) * progress,
        y: from.y + (to.y - from.y) * progress,
      }
      if (!this.isWorldPositionWalkable(point, agent)) return false
    }
    return true
  }

  canDragFrom(world: { x: number; y: number }, agent: NavigationAgent) {
    const centerX = world.x + (agent.offsetX ?? 0)
    const centerY = world.y + (agent.offsetY ?? 0)
    return centerX - agent.width / 2 >= this.originX &&
      centerX + agent.width / 2 <= this.originX + this.width * this.tileWidth &&
      centerY - agent.height / 2 >= this.originY &&
      centerY + agent.height / 2 <= this.originY + this.height * this.tileHeight &&
      this.isWorldPositionWalkable(world, agent)
  }

  constrainDrag(from: { x: number; y: number }, target: { x: number; y: number }, agent: NavigationAgent) {
    return sweepMovement(from, target, agent, {
      x: this.originX, y: this.originY,
      width: this.width * this.tileWidth, height: this.height * this.tileHeight,
    }, this.blockedRects)
  }

  private isWalkableCell(cell: Cell, agent?: NavigationAgent) {
    if (this.isBlocked(cell)) return false
    return this.isWorldPositionWalkable(this.cellToWorld(cell), agent)
  }

  private getNeighbors(cell: Cell, agent?: NavigationAgent) {
    const candidates: Cell[] = [
      { x: cell.x + 1, y: cell.y },
      { x: cell.x - 1, y: cell.y },
      { x: cell.x, y: cell.y + 1 },
      { x: cell.x, y: cell.y - 1 },
    ]

    return candidates.filter(
      (item) =>
        item.x >= 0 &&
        item.y >= 0 &&
        item.x < this.width &&
        item.y < this.height &&
        this.isWalkableCell(item, agent) &&
        this.canTraverseWorld(this.cellToWorld(cell), this.cellToWorld(item), agent)
    )
  }

  private findNearestWalkable(cell: Cell, agent?: NavigationAgent) {
    if (this.isWalkableCell(cell, agent)) return cell

    const visited = new Set<string>([toCellKey(cell)])
    const queue: Cell[] = [cell]

    while (queue.length > 0) {
      const current = queue.shift()!
      const candidates: Cell[] = [
        { x: current.x + 1, y: current.y },
        { x: current.x - 1, y: current.y },
        { x: current.x, y: current.y + 1 },
        { x: current.x, y: current.y - 1 },
      ]

      for (const candidate of candidates) {
        if (
          candidate.x < 0 ||
          candidate.y < 0 ||
          candidate.x >= this.width ||
          candidate.y >= this.height
        ) {
          continue
        }
        const key = toCellKey(candidate)
        if (visited.has(key)) continue
        if (this.isWalkableCell(candidate, agent)) return candidate
        visited.add(key)
        queue.push(candidate)
      }
    }

    return cell
  }

  nearestWalkableWorld(world: { x: number; y: number }, agent?: NavigationAgent) {
    const walkableCell = this.findNearestWalkable(this.worldToCell(world.x, world.y), agent)
    return this.cellToWorld(walkableCell)
  }

  randomWalkableWorld(agent?: NavigationAgent, random = Math.random) {
    const maxAttempts = Math.max(64, this.width * this.height)
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const cell = {
        x: Math.floor(random() * this.width),
        y: Math.floor(random() * this.height),
      }
      if (this.isWalkableCell(cell, agent)) return this.cellToWorld(cell)
    }
    return undefined
  }

  findRoute(
    startWorld: { x: number; y: number },
    targetWorld: { x: number; y: number },
    agent?: NavigationAgent
  ) {
    const start = this.findNearestWalkable(this.worldToCell(startWorld.x, startWorld.y), agent)
    const goal = this.findNearestWalkable(this.worldToCell(targetWorld.x, targetWorld.y), agent)

    if (!this.isWalkableCell(start, agent) || !this.isWalkableCell(goal, agent)) {
      return {
        waypoints: [],
        resolvedGoal: this.cellToWorld(goal),
      }
    }

    const resolvedGoal = this.cellToWorld(goal)
    const open: Cell[] = [start]
    const cameFrom = new Map<string, string>()
    const gScore = new Map<string, number>([[toCellKey(start), 0]])
    const fScore = new Map<string, number>([[toCellKey(start), manhattan(start, goal)]])
    const openSet = new Set<string>([toCellKey(start)])

    while (open.length > 0) {
      let currentIndex = 0
      for (let i = 1; i < open.length; i += 1) {
        const currentKey = toCellKey(open[currentIndex])
        const compareKey = toCellKey(open[i])
        if (
          (fScore.get(compareKey) ?? Number.POSITIVE_INFINITY) <
          (fScore.get(currentKey) ?? Number.POSITIVE_INFINITY)
        ) {
          currentIndex = i
        }
      }

      const current = open.splice(currentIndex, 1)[0]
      const currentKey = toCellKey(current)
      openSet.delete(currentKey)

      if (current.x === goal.x && current.y === goal.y) {
        const cells: Cell[] = [current]
        let walkKey = currentKey
        while (cameFrom.has(walkKey)) {
          const parentKey = cameFrom.get(walkKey)!
          const [x, y] = parentKey.split(',').map(Number)
          cells.unshift({ x, y })
          walkKey = parentKey
        }
        return {
          waypoints: cells.map((cell) => this.cellToWorld(cell)),
          resolvedGoal,
        }
      }

      this.getNeighbors(current, agent).forEach((neighbor) => {
        const neighborKey = toCellKey(neighbor)
        const tentativeG = (gScore.get(currentKey) ?? Number.POSITIVE_INFINITY) + 1
        if (tentativeG >= (gScore.get(neighborKey) ?? Number.POSITIVE_INFINITY)) return

        cameFrom.set(neighborKey, currentKey)
        gScore.set(neighborKey, tentativeG)
        fScore.set(neighborKey, tentativeG + manhattan(neighbor, goal))

        if (!openSet.has(neighborKey)) {
          open.push(neighbor)
          openSet.add(neighborKey)
        }
      })
    }

    return {
      waypoints: [],
      resolvedGoal,
    }
  }

  findPath(
    startWorld: { x: number; y: number },
    targetWorld: { x: number; y: number },
    agent?: NavigationAgent
  ) {
    return this.findRoute(startWorld, targetWorld, agent).waypoints
  }
}
