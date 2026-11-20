import { t, i18n } from '../i18n'
import Phaser from 'phaser'

import { createCharacterAnims } from '../anims/CharacterAnims'
import { createEmployeeReactionAnims } from '../anims/EmployeeReactionAnims'
import { createEmployeePickupAnims } from '../anims/EmployeePickupAnims'

import Item from '../items/Item'
import Chair from '../items/Chair'
import Computer from '../items/Computer'
import Whiteboard from '../items/Whiteboard'
import VendingMachine from '../items/VendingMachine'
import '../characters/MyPlayer'
import MyPlayer from '../characters/MyPlayer'
import OfficeEmployee, { type IdleSeat } from '../characters/OfficeEmployee'
import PlayerSelector from '../characters/PlayerSelector'
import { additionalCharacterTextures } from '../characters/additionalCharacterTextures'
import { hasDailyCharacterAssets } from '../characters/dailyCharacterAssets'
import { officeApi, type OfficeEmployeeSummary } from '../services/office/officeApi'
import { PlayerBehavior } from '../types/PlayerBehavior'
import { NavKeys, Keyboard } from '../types/KeyboardState'
import {
  isOfficeExpansionTileCollidable,
  OFFICE_EXPANSION_COLUMNS,
  OFFICE_EXPANSION_GIDS,
  OFFICE_EXPANSION_LEFT_COLUMNS,
  OFFICE_EXPANSION_ORIGIN_X,
  OFFICE_EXPANSION_ORIGIN_Y,
  OFFICE_EXPANSION_TILE_SIZE,
  OFFICE_EXPANSION_WALL_OVERLAYS,
  OFFICE_EXPANSION_WINDOW_GIDS,
  OFFICE_EXPANSION_WINDOWS,
} from './officeExpansion'
import { getEmployeeWorkstation, listEmployeeWorkstations } from './employeeWorkstations'
import { PathfindingGrid } from '../utils/pathfinding'
import type { WorkstationSnapshot } from '../workstations/workstationModel'
import { useWorkstationState } from '../workstations/workstationState'

type Point = {
  x: number
  y: number
}

type SeatDirection = 'up' | 'down' | 'left' | 'right'

type BlockedRect = {
  x: number
  y: number
  width: number
  height: number
}

type WorkstationCopyRegion = {
  sourceAnchor: BlockedRect
  sourceBounds: BlockedRect
  target: Point
  rotation: number
}

const copiedWorkstationRegions: WorkstationCopyRegion[] = [
  {
    sourceAnchor: { x: 928, y: 480, width: 320, height: 160 },
    sourceBounds: { x: 928, y: 416, width: 320, height: 160 },
    target: { x: OFFICE_EXPANSION_ORIGIN_X, y: 192 },
    rotation: 0,
  },
  {
    sourceAnchor: { x: 928, y: 736, width: 320, height: 160 },
    sourceBounds: { x: 928, y: 672, width: 320, height: 160 },
    target: { x: OFFICE_EXPANSION_ORIGIN_X, y: 416 },
    rotation: 0,
  },
]

const oppositeSeatDirection: Record<SeatDirection, SeatDirection> = {
  up: 'down',
  down: 'up',
  left: 'right',
  right: 'left',
}

// Idle employees may walk through the conference room, but they must not
// choose one of its chairs or stop inside the room. The conference room is
// the lower central room around the long table, not the upper lounge area.
const idleForbiddenAreas = [
  { x: 192, y: 576, width: 416, height: 192 },
]

const copiedWorkstationSidePanelGids = new Set([3000, 3016, 3032])
// These side-panel tiles draw the visible vertical bar on the tile's right edge.
// Keep it mostly inside the workstation edge, then nudge it right 6px for visual contact.
const copiedWorkstationSidePanelRightNudgePx = 6
const copiedWorkstationSidePanelRightInset =
  OFFICE_EXPANSION_TILE_SIZE - copiedWorkstationSidePanelRightNudgePx

export default class Game extends Phaser.Scene {
  private cursors!: NavKeys
  private keyE!: Phaser.Input.Keyboard.Key
  private keyR!: Phaser.Input.Keyboard.Key
  private map!: Phaser.Tilemaps.Tilemap
  myPlayer!: MyPlayer
  private playerSelector!: PlayerSelector
  private officeEmployees = new Map<string, OfficeEmployee>()
  private officeEmployeeGroup!: Phaser.Physics.Arcade.Group
  private officeNavigation!: PathfindingGrid
  private idleSeats: IdleSeat[] = []
  private occupiedIdleSeats = new Set<string>()
  private officeEmployeeSyncTimer?: Phaser.Time.TimerEvent
  private officeEmployeeSyncInFlight = false
  private workstationComputers = new Map<string, Computer>()
  private workstationSyncElapsed = 0
  private handleOfficeEmployeesUpdated = (event: Event) => {
    const employees = (event as CustomEvent<OfficeEmployeeSummary[]>).detail
    if (Array.isArray(employees)) this.syncOfficeEmployees(employees)
  }
  constructor() {
    super('game')
  }

  registerKeys() {
    this.cursors = {
      ...this.input.keyboard.createCursorKeys(),
      ...(this.input.keyboard.addKeys('W,S,A,D') as Keyboard),
    }

    this.keyE = this.input.keyboard.addKey('E')
    this.keyR = this.input.keyboard.addKey('R')
    this.input.keyboard.disableGlobalCapture()
  }

  disableKeys() {
    this.input.keyboard.enabled = false
  }

  enableKeys() {
    this.input.keyboard.enabled = true
  }

  create() {
    createCharacterAnims(this.anims)
    createEmployeeReactionAnims(this)
    createEmployeePickupAnims(this)
    this.registerKeys()
    // Employees can walk under a stationary cursor.
    this.input.setPollAlways()

    this.map = this.make.tilemap({ key: 'tilemap' })
    const floorAndGround = this.map.addTilesetImage('FloorAndGround', 'tiles_wall')

    const groundLayer = this.map.createLayer('Ground', floorAndGround)
    groundLayer.setCollisionByProperty({ collides: true })

    const officeExpansion = this.createOfficeExpansion(groundLayer)
    const copiedCollidableOfficeObjects = this.getCopiedWorkstationObjects('ObjectsOnCollide')
    const navigationBlockedRects = [
      ...this.tileLayerToRects(groundLayer),
      ...this.objectLayerToRects('Wall'),
      ...officeExpansion.blockedRects,
      ...this.objectLayerToRects('ObjectsOnCollide', copiedCollidableOfficeObjects),
      ...this.objectLayerToRects('GenericObjectsOnCollide'),
      ...this.objectLayerToRects('Basement'),
      ...this.objectLayerToRects('VendingMachine'),
    ]
    this.officeNavigation = new PathfindingGrid({
      width: Math.ceil((this.map.widthInPixels - OFFICE_EXPANSION_ORIGIN_X) / OFFICE_EXPANSION_TILE_SIZE),
      height: Math.ceil(this.map.heightInPixels / OFFICE_EXPANSION_TILE_SIZE),
      tileWidth: OFFICE_EXPANSION_TILE_SIZE,
      tileHeight: OFFICE_EXPANSION_TILE_SIZE,
      originX: OFFICE_EXPANSION_ORIGIN_X,
      originY: 0,
      blockedRects: navigationBlockedRects,
      obstaclePadding: 0,
    })
    this.physics.world.setBounds(
      OFFICE_EXPANSION_ORIGIN_X,
      0,
      this.map.widthInPixels - OFFICE_EXPANSION_ORIGIN_X,
      this.map.heightInPixels
    )

    this.myPlayer = this.add.myPlayer(705, 500, 'adam')
    this.officeEmployeeGroup = this.physics.add.group({ runChildUpdate: false })
    this.myPlayer.setPlayerName(t('ui.me'))
    const updatePlayerLanguage = () => this.myPlayer.setPlayerName(t('ui.me'))
    i18n.on('languageChanged', updatePlayerLanguage)
    this.events.once('shutdown', () => i18n.off('languageChanged', updatePlayerLanguage))
    this.playerSelector = new PlayerSelector(this, 0, 0, 16, 16)
    window.addEventListener('office-employees-updated', this.handleOfficeEmployeesUpdated)
    this.officeEmployeeSyncTimer = this.time.addEvent({
      delay: 1000,
      loop: true,
      callback: () => { void this.refreshOfficeEmployees() },
    })
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      window.removeEventListener('office-employees-updated', this.handleOfficeEmployeesUpdated)
      this.officeEmployeeSyncTimer?.remove(false)
      this.officeEmployeeSyncTimer = undefined
      this.officeEmployees.forEach(employee => employee.destroy())
      this.officeEmployees.clear()
    })

    const chairs = this.physics.add.staticGroup({ classType: Chair })
    const chairLayer = this.map.getObjectLayer('Chair')
    const copiedChairObjects = this.getCopiedWorkstationObjects('Chair')
    const chairObjects = [...chairLayer.objects, ...copiedChairObjects]
    chairObjects.forEach((chairObj) => {
      const item = this.addObjectFromTiled(chairs, chairObj, 'chairs', 'chair') as Chair
      const direction = chairObj.properties?.find((property) => property.name === 'direction')
        ?.value as SeatDirection
      item.itemDirection = direction
    })
    this.idleSeats = this.createIdleSeats(chairObjects)

    const computers = this.physics.add.staticGroup({ classType: Computer })
    const computerLayer = this.map.getObjectLayer('Computer')
    const copiedComputerObjects = this.getCopiedWorkstationObjects('Computer')
    const computerObjects = [...computerLayer.objects, ...copiedComputerObjects]
    computerObjects.forEach((obj) => {
      const item = this.addObjectFromTiled(computers, obj, 'computers', 'computer') as Computer
      item.setDepth(item.y + item.height * 0.27)
    })
    this.workstationComputers.clear()
    const mapComputers = computers.getChildren() as Computer[]
    for (const workstation of listEmployeeWorkstations()) {
      const chair = (chairs.getChildren() as Chair[]).find(item => item.x === workstation.x && item.y === workstation.y && item.itemDirection === workstation.direction)
      const computerY = workstation.y + (workstation.direction === 'up' ? -32 : 64)
      const source = mapComputers.find(item => item.x === workstation.x && item.y === computerY)
      if (!chair || !source) throw new Error(`Missing map objects for ${workstation.id}`)
      const computer: Computer = workstation.direction === 'up' ? source : computers.get(workstation.x, workstation.y + 32, 'computers', source.frame.name).setVisible(false)
      computer.workstation = workstation
      computer.openPreview = () => this.openSelectedWorkstation(computer)
      ;(computer.body as Phaser.Physics.Arcade.StaticBody).setSize(80, 32)
      ;(chair.body as Phaser.Physics.Arcade.StaticBody).setSize(32, 24)
      chair.computer = computer
      this.workstationComputers.set(workstation.id, computer)
    }
    let savedInput: { pointer: boolean; keyboard: boolean } | undefined
    const releaseWorkstation = useWorkstationState.subscribe((state, previous) => {
      if (Boolean(state.active) === Boolean(previous.active)) return
      if (state.active) {
        savedInput = { pointer: this.input.enabled, keyboard: this.input.keyboard.enabled }
        this.input.keyboard.resetKeys()
        this.input.keyboard.enabled = false
        this.input.enabled = false
        this.officeEmployees.forEach(employee => employee.setInteractionsSuspended(true))
        this.myPlayer.setVelocity(0, 0)
        ;(this.myPlayer.playerContainer.body as Phaser.Physics.Arcade.Body).setVelocity(0, 0)
      } else if (savedInput) {
        this.input.keyboard.resetKeys()
        this.input.enabled = savedInput.pointer
        this.input.keyboard.enabled = savedInput.keyboard
        this.officeEmployees.forEach(employee => employee.setInteractionsSuspended(false))
        savedInput = undefined
      }
    })
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      useWorkstationState.getState().close()
      releaseWorkstation()
    })

    const whiteboards = this.physics.add.staticGroup({ classType: Whiteboard })
    const whiteboardLayer = this.map.getObjectLayer('Whiteboard')
    whiteboardLayer.objects.forEach((obj) => {
      this.addObjectFromTiled(whiteboards, obj, 'whiteboards', 'whiteboard')
    })

    const vendingMachines = this.physics.add.staticGroup({ classType: VendingMachine })
    const vendingMachineLayer = this.map.getObjectLayer('VendingMachine')
    vendingMachineLayer.objects.forEach((obj) => {
      this.addObjectFromTiled(vendingMachines, obj, 'vendingmachines', 'vendingmachine')
    })

    const copiedOfficeObjects = this.getCopiedWorkstationObjects('Objects')

    this.addGroupFromTiled('Wall', 'tiles_wall', 'FloorAndGround', false)
    this.addGroupFromTiled(
      'Objects',
      'office',
      'Modern_Office_Black_Shadow',
      false,
      copiedOfficeObjects
    )
    this.addGroupFromTiled(
      'ObjectsOnCollide',
      'office',
      'Modern_Office_Black_Shadow',
      true,
      copiedCollidableOfficeObjects
    )
    this.addGroupFromTiled('GenericObjects', 'generic', 'Generic', false)
    this.addGroupFromTiled('GenericObjectsOnCollide', 'generic', 'Generic', true)
    this.addGroupFromTiled('Basement', 'basement', 'Basement', true)


    this.cameras.main.zoom = 1.5
    this.cameras.main.startFollow(this.myPlayer, true)

    this.physics.add.collider([this.myPlayer, this.myPlayer.playerContainer], groundLayer)
    this.physics.add.collider(
      [this.myPlayer, this.myPlayer.playerContainer],
      officeExpansion.colliders
    )
    this.physics.add.collider([this.myPlayer, this.myPlayer.playerContainer], vendingMachines)

    this.physics.add.overlap(
      this.playerSelector,
      [chairs, computers, whiteboards, vendingMachines],
      this.handleItemSelectorOverlap,
      undefined,
      this
    )

    void officeApi.listEmployees()
      .then((employees) => { if (this.scene.isActive()) this.syncOfficeEmployees(employees) })
      .catch((error) => console.warn('Unable to load Office employee projection:', error))

  }

  private createIdleSeats(chairObjects: Phaser.Types.Tilemaps.TiledObject[]): IdleSeat[] {
    const workstations = listEmployeeWorkstations()

    return chairObjects.flatMap((chair, index) => {
      const direction = chair.properties?.find((property) => property.name === 'direction')
        ?.value as SeatDirection | undefined
      if (!direction) return []

      const x = (chair.x ?? 0) + (chair.width ?? this.map.tileWidth) * 0.5
      const y = (chair.y ?? 0) - (chair.height ?? this.map.tileHeight) * 0.5
      // A desk group can have a second decorative chair whose anchor is
      // offset from the bound workstation. Exclude the whole workstation
      // cluster so idle employees never sit back at a computer desk.
      const isWorkstation = chair.properties?.some((property) => (
        property.name === 'workstation' && property.value === true
      )) || workstations.some((workstation) => (
        Math.hypot(workstation.x - x, workstation.y - y) <= 112
      ))
      const isMeetingRoomSeat = idleForbiddenAreas.some((area) => (
        x >= area.x &&
        x <= area.x + area.width &&
        y >= area.y &&
        y <= area.y + area.height
      ))
      if (isWorkstation || isMeetingRoomSeat) return []

      return [{
        id: `idle-seat-${chair.id ?? index}-${index}`,
        x,
        y,
        direction,
      }]
    })
  }

  private textureForEmployee({ id, avatar }: OfficeEmployeeSummary) {
    if (hasDailyCharacterAssets(avatar)) return avatar
    const hash = [...id].reduce((total, character) => total + character.charCodeAt(0), 0)
    return additionalCharacterTextures[hash % additionalCharacterTextures.length]
  }

  private async refreshOfficeEmployees() {
    if (this.officeEmployeeSyncInFlight) return
    this.officeEmployeeSyncInFlight = true
    try {
      const employees = await officeApi.listEmployees()
      if (this.scene.isActive()) this.syncOfficeEmployees(employees)
    } catch (error) {
      console.warn('Unable to refresh Office employee projection:', error)
    } finally {
      this.officeEmployeeSyncInFlight = false
    }
  }

  private syncOfficeEmployees(projections: OfficeEmployeeSummary[]) {
    const nextIds = new Set<string>()
    projections.forEach((projection) => {
      const workstation = getEmployeeWorkstation(projection.workstationId)
      if (!workstation) {
        console.warn(`Skipping Office employee without a valid workstation: ${projection.id}`)
        return
      }
      nextIds.add(projection.id)
      const existing = this.officeEmployees.get(projection.id)
      if (existing) {
        existing.applyProjection(projection)
        return
      }
      const employee = new OfficeEmployee(
        this,
        projection,
        workstation,
        this.textureForEmployee(projection),
        this.officeNavigation,
        this.idleSeats,
        this.occupiedIdleSeats,
        idleForbiddenAreas,
      )
      this.officeEmployeeGroup.add(employee)
      employee.setInteractionsSuspended(Boolean(useWorkstationState.getState().active))
      this.officeEmployees.set(projection.id, employee)
    })
    this.officeEmployees.forEach((employee, employeeId) => {
      if (nextIds.has(employeeId)) return
      employee.destroy()
      this.officeEmployees.delete(employeeId)
    })
  }

  getOfficeEmployeeSnapshot() {
    return [...this.officeEmployees.values()].map((employee) => ({
      id: employee.employeeId,
      workstationId: employee.workstation.id,
      x: employee.x,
      y: employee.y,
      state: employee.getProjection().state,
    }))
  }


  private handleItemSelectorOverlap(playerSelector, selectionItem) {
    const computer = selectionItem instanceof Computer ? selectionItem : selectionItem instanceof Chair ? selectionItem.computer : undefined
    if (computer && !computer.canInteractFrom(this.myPlayer)) return
    if (this.myPlayer.playerBehavior === PlayerBehavior.SITTING && selectionItem instanceof Chair) return
    const currentItem = playerSelector.selectedItem as Item
    if (currentItem) {
      if (currentItem === selectionItem) return
      const currentComputer = currentItem instanceof Computer ? currentItem : currentItem instanceof Chair ? currentItem.computer : undefined
      if (computer && currentComputer) {
        const currentDistance = Phaser.Math.Distance.Between(playerSelector.x, playerSelector.y, currentItem.x, currentItem.y)
        const nextDistance = Phaser.Math.Distance.Between(playerSelector.x, playerSelector.y, selectionItem.x, selectionItem.y)
        if (currentComputer.canInteractFrom(this.myPlayer) && this.physics.overlap(playerSelector, currentItem) && currentDistance <= nextDistance) return
      } else if (currentItem.depth >= selectionItem.depth) {
        return
      }
      if (this.myPlayer.playerBehavior !== PlayerBehavior.SITTING) currentItem.clearDialogBox()
    }

    playerSelector.selectedItem = selectionItem
    selectionItem.onOverlapDialog()
  }

  private addObjectFromTiled(
    group: Phaser.Physics.Arcade.StaticGroup,
    object: Phaser.Types.Tilemaps.TiledObject,
    key: string,
    tilesetName: string
  ) {
    const actualX = object.x! + object.width! * 0.5
    const actualY = object.y! - object.height! * 0.5
    const item = group
      .get(actualX, actualY, key, object.gid! - this.map.getTileset(tilesetName).firstgid)
      .setDepth(actualY)

    if (object.rotation) {
      item.setRotation(Phaser.Math.DegToRad(object.rotation))
    }

    return item
  }

  private getCopiedWorkstationObjects(objectLayerName: string) {
    const objectLayer = this.map.getObjectLayer(objectLayerName)
    return copiedWorkstationRegions.flatMap((region) =>
      objectLayer.objects
        .filter((object) => this.isObjectAnchorInRect(object, region.sourceAnchor))
        .map((object) => this.cloneTiledObject(object, region, objectLayerName))
    )
  }

  private isObjectAnchorInRect(object: Phaser.Types.Tilemaps.TiledObject, rect: BlockedRect) {
    const x = object.x ?? 0
    const y = object.y ?? 0
    return x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height
  }

  private cloneTiledObject(
    object: Phaser.Types.Tilemaps.TiledObject,
    region: WorkstationCopyRegion,
    objectLayerName: string
  ) {
    const width = object.width ?? this.map.tileWidth
    const height = object.height ?? this.map.tileHeight
    const normalizedRotation = ((region.rotation % 360) + 360) % 360
    const isHalfTurn = normalizedRotation === 180
    const relativeX = (object.x ?? 0) - region.sourceBounds.x
    const relativeY = (object.y ?? 0) - region.sourceBounds.y
    const shouldMoveSidePanelToRight =
      objectLayerName === 'Objects' &&
      !isHalfTurn &&
      copiedWorkstationSidePanelGids.has(object.gid ?? -1) &&
      (object.x ?? 0) === region.sourceBounds.x
    let copiedX = isHalfTurn
      ? region.target.x + region.sourceBounds.width - relativeX - width
      : region.target.x + relativeX

    if (shouldMoveSidePanelToRight) {
      copiedX =
        region.target.x +
        region.sourceBounds.width -
        copiedWorkstationSidePanelRightInset
    }

    const copiedY = isHalfTurn
      ? region.target.y + region.sourceBounds.height - relativeY + height
      : region.target.y + relativeY

    return {
      ...object,
      x: copiedX,
      y: copiedY,
      rotation: ((object.rotation ?? 0) + region.rotation) % 360,
      properties: object.properties?.map((property) => {
        if (
          isHalfTurn &&
          property.name === 'direction' &&
          typeof property.value === 'string'
        ) {
          return {
            ...property,
            value:
              oppositeSeatDirection[property.value as SeatDirection] ??
              (property.value as SeatDirection),
          }
        }

        return { ...property }
      }),
    }
  }

  private createOfficeExpansion(groundLayer: Phaser.Tilemaps.TilemapLayer) {
    const colliders = this.physics.add.staticGroup()
    const blockedRects: BlockedRect[] = []
    const blockedCells = new Set<string>()
    const sharedMapColumn =
      (OFFICE_EXPANSION_ORIGIN_X +
        (OFFICE_EXPANSION_COLUMNS - 1) * OFFICE_EXPANSION_TILE_SIZE) /
      OFFICE_EXPANSION_TILE_SIZE
    const sharedMapRow = OFFICE_EXPANSION_ORIGIN_Y / OFFICE_EXPANSION_TILE_SIZE

    const addBlockedCell = (col: number, row: number) => {
      const key = `${col}:${row}`
      if (blockedCells.has(key)) return
      blockedCells.add(key)

      const x = OFFICE_EXPANSION_ORIGIN_X + col * OFFICE_EXPANSION_TILE_SIZE
      const y = OFFICE_EXPANSION_ORIGIN_Y + row * OFFICE_EXPANSION_TILE_SIZE
      blockedRects.push({
        x,
        y,
        width: OFFICE_EXPANSION_TILE_SIZE,
        height: OFFICE_EXPANSION_TILE_SIZE,
      })
      colliders
        .create(
          x + OFFICE_EXPANSION_TILE_SIZE / 2,
          y + OFFICE_EXPANSION_TILE_SIZE / 2,
          'tiles_wall',
          0
        )
        .setVisible(false)
    }

    OFFICE_EXPANSION_GIDS.forEach((rowGids, row) => {
      rowGids.forEach((gid, col) => {
        const worldX = OFFICE_EXPANSION_ORIGIN_X + col * OFFICE_EXPANSION_TILE_SIZE
        const worldY = OFFICE_EXPANSION_ORIGIN_Y + row * OFFICE_EXPANSION_TILE_SIZE
        const collidable = isOfficeExpansionTileCollidable(gid)

        if (col === OFFICE_EXPANSION_COLUMNS - 1) {
          const mapRow = sharedMapRow + row
          if (gid === 0) {
            groundLayer.removeTileAt(sharedMapColumn, mapRow)
          } else {
            const tile = groundLayer.putTileAt(gid, sharedMapColumn, mapRow)
            tile.flipX = true
            if (collidable) {
              tile.setCollision(true, true, true, true)
            } else {
              tile.resetCollision()
            }
          }
          return
        }

        if (gid > 0) {
          this.add
            .image(worldX, worldY, 'tiles_wall', gid - 1)
            .setOrigin(0)
            .setFlipX(true)
            .setDepth(0)
        }
        if (collidable) addBlockedCell(col, row)
      })
    })

    OFFICE_EXPANSION_WALL_OVERLAYS.forEach(({ col, row, gid }) => {
      const worldX = OFFICE_EXPANSION_ORIGIN_X + col * OFFICE_EXPANSION_TILE_SIZE
      const worldY = OFFICE_EXPANSION_ORIGIN_Y + row * OFFICE_EXPANSION_TILE_SIZE
      this.add
        .image(worldX, worldY, 'tiles_wall', gid - 1)
        .setOrigin(0)
        .setFlipX(true)
        .setDepth(worldY + OFFICE_EXPANSION_TILE_SIZE)

      if (isOfficeExpansionTileCollidable(gid)) addBlockedCell(col, row)
    })

    const expansionWindows = this.physics.add.staticGroup()
    OFFICE_EXPANSION_WINDOWS.forEach(({ col, row }) => {
      OFFICE_EXPANSION_WINDOW_GIDS.forEach((rowGids, rowOffset) => {
        rowGids.forEach((gid, colOffset) => {
          const worldX =
            OFFICE_EXPANSION_ORIGIN_X + (col + colOffset) * OFFICE_EXPANSION_TILE_SIZE
          const worldY =
            OFFICE_EXPANSION_ORIGIN_Y + (row + rowOffset) * OFFICE_EXPANSION_TILE_SIZE
          this.addObjectFromTiled(
            expansionWindows,
            {
              x: worldX,
              y: worldY + OFFICE_EXPANSION_TILE_SIZE,
              width: OFFICE_EXPANSION_TILE_SIZE,
              height: OFFICE_EXPANSION_TILE_SIZE,
              gid,
            } as Phaser.Types.Tilemaps.TiledObject,
            'generic',
            'Generic'
          )
        })
      })
    })

    return { colliders, blockedRects }
  }

  private addGroupFromTiled(
    objectLayerName: string,
    key: string,
    tilesetName: string,
    collidable: boolean,
    additionalObjects: Phaser.Types.Tilemaps.TiledObject[] = []
  ) {
    const group = this.physics.add.staticGroup()
    const objectLayer = this.map.getObjectLayer(objectLayerName)
    const objects = [...objectLayer.objects, ...additionalObjects]
    objects.forEach((object) => {
      this.addObjectFromTiled(group, object, key, tilesetName)
    })
    if (this.myPlayer && collidable) {
      this.physics.add.collider([this.myPlayer, this.myPlayer.playerContainer], group)
    }
    return group
  }

  private tileLayerToRects(layer: Phaser.Tilemaps.TilemapLayer) {
    const rects: BlockedRect[] = []
    layer.forEachTile((tile) => {
      if (!tile.collides) return
      rects.push({
        x: tile.pixelX,
        y: tile.pixelY,
        width: tile.width,
        height: tile.height,
      })
    })
    return rects
  }

  private objectLayerToRects(
    layerName: string,
    additionalObjects: Phaser.Types.Tilemaps.TiledObject[] = []
  ) {
    const layer = this.map.getObjectLayer(layerName)
    return this.objectsToRects([...layer.objects, ...additionalObjects])
  }

  private objectsToRects(objects: Phaser.Types.Tilemaps.TiledObject[]) {
    return objects.map((object) => this.objectToRect(object))
  }

  private objectToRect(object: Phaser.Types.Tilemaps.TiledObject) {
    return {
      x: object.x ?? 0,
      y: (object.y ?? 0) - (object.height ?? 0),
      width: object.width ?? this.map.tileWidth,
      height: object.height ?? this.map.tileHeight,
    }
  }

  private workstationSnapshot(computer: Computer): WorkstationSnapshot {
    const workstationId = computer.workstation.id
    const employee = [...this.officeEmployees.values()].find(item => item.getProjection().workstationId === workstationId)
    const projection = employee?.getProjection()
    return {
      workstationId,
      computerFrame: Number(computer.frame.name),
      employeeId: projection?.id,
      displayName: projection?.displayName,
      characterTexture: employee?.playerTexture,
      state: projection?.state ?? 'unbound',
      atDesk: employee?.workstation.id === workstationId && employee.isOperatingWorkstation(),
    }
  }

  private openSelectedWorkstation(computer: Computer) {
    const focused = document.activeElement
    const editing = focused instanceof HTMLElement && (focused.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(focused.tagName))
    const dialogOpen = [...document.querySelectorAll<HTMLElement>('[role="dialog"]')]
      .some(dialog => dialog.getClientRects().length > 0 && getComputedStyle(dialog).visibility === 'visible')
    const selected = this.playerSelector.selectedItem
    if (!editing && !dialogOpen && !useWorkstationState.getState().active &&
      this.input.enabled && this.input.keyboard.enabled && selected === computer &&
      computer.canInteractFrom(this.myPlayer) && this.physics.overlap(this.playerSelector, selected)) {
      useWorkstationState.getState().open(this.workstationSnapshot(computer))
      return true
    }
    return false
  }

  update(_time?: number, delta = 16) {
    const workstationOpen = Boolean(useWorkstationState.getState().active)
    if (this.myPlayer && !workstationOpen) {
      this.playerSelector.update(this.myPlayer, this.cursors)
      this.myPlayer.update(this.playerSelector, this.cursors, this.keyE, this.keyR)
    }
    this.officeEmployees.forEach((employee) => employee.updateOffice(delta))
    if (workstationOpen) {
      this.workstationSyncElapsed += delta
      if (this.workstationSyncElapsed >= 150) {
        this.workstationSyncElapsed = 0
        const active = useWorkstationState.getState().active!
        const computer = this.workstationComputers.get(active.workstationId)!
        useWorkstationState.getState().sync(this.workstationSnapshot(computer))
      }
    }
  }
}
