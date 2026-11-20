import { ItemType } from '../types/Items'
import Item from './Item'
import { canReachWorkstation, type EmployeeWorkstation } from '../scenes/employeeWorkstations'

export default class Computer extends Item {
  workstation!: EmployeeWorkstation
  openPreview!: () => boolean

  constructor(scene: Phaser.Scene, x: number, y: number, texture: string, frame?: string | number) {
    super(scene, x, y, texture, frame)

    this.itemType = ItemType.COMPUTER
  }

  onOverlapDialog() {
    this.setDialogBox('ui.press_r_to_view_workstation')
  }

  protected get dialogOffsetY() {
    return this.height * (this.workstation.direction === 'up' ? 0.5 : -0.5)
  }

  viewWorkstation() {
    return this.openPreview()
  }

  canInteractFrom(player: { x: number; y: number }) {
    return canReachWorkstation(this.workstation, player)
  }
}
