export type ExpansionWallOverlay = {
  col: number
  row: number
  gid: number
}

export type ExpansionWindow = {
  col: number
  row: number
}

export const OFFICE_EXPANSION_ORIGIN_X = -320
export const OFFICE_EXPANSION_ORIGIN_Y = 128
export const OFFICE_EXPANSION_TILE_SIZE = 32
export const OFFICE_EXPANSION_COLUMNS = 16
export const OFFICE_EXPANSION_ROWS = 21
export const OFFICE_EXPANSION_LEFT_COLUMNS = Math.abs(
  OFFICE_EXPANSION_ORIGIN_X / OFFICE_EXPANSION_TILE_SIZE
)

// Mirrored from the existing right-side office shell. The original upper doorway is
// closed. The existing corridor wall is extended left through rows 6-7, and the doorway
// remains below it on rows 8-10.
export const OFFICE_EXPANSION_GIDS: number[][] = [
  [90, 722, 722, 722, 722, 722, 722, 722, 722, 722, 722, 722, 722, 722, 722, 28],
  [154, 786, 786, 786, 786, 786, 786, 786, 786, 786, 786, 786, 786, 786, 786, 92],
  [154, 348, 348, 348, 348, 348, 348, 348, 348, 348, 348, 348, 348, 348, 347, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 411, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 411, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 411, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 411, 722],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 411, 786],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 349, 348],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 0],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [154, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 412, 92],
  [218, 217, 217, 217, 217, 217, 217, 217, 217, 217, 217, 217, 217, 217, 217, 85],
]

export const OFFICE_EXPANSION_WINDOW_GIDS = [
  [4130, 4131],
  [4146, 4147],
]

export const OFFICE_EXPANSION_WINDOWS: ExpansionWindow[] = [
  { col: 8, row: 0 },
  { col: 12, row: 0 },
]

export const OFFICE_EXPANSION_WALL_OVERLAYS: ExpansionWallOverlay[] = [
  { col: 15, row: 11, gid: 149 },
]

const WALKABLE_GIDS = new Set([0, 347, 348, 349, 411, 412])

export function isOfficeExpansionTileCollidable(gid: number) {
  return gid > 0 && !WALKABLE_GIDS.has(gid)
}