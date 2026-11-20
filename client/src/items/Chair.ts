import { ItemType } from '../types/Items'
import Item from './Item'
import type Computer from './Computer'

export default class Chair extends Item {
  itemDirection?: string
  computer?: Computer

  constructor(scene: Phaser.Scene, x: number, y: number, texture: string, frame?: string | number) {
    super(scene, x, y, texture, frame)

    this.itemType = ItemType.CHAIR
  }

  onOverlapDialog() {
    this.setDialogBox('ui.press_e_to_sit')
  }
}
