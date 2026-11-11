import { ItemType } from '../types/Items'
import Item from './Item'

export default class Whiteboard extends Item {
  constructor(scene: Phaser.Scene, x: number, y: number, texture: string, frame?: string | number) {
    super(scene, x, y, texture, frame)

    this.itemType = ItemType.WHITEBOARD
  }

  onOverlapDialog() {
    this.setDialogBox('ui.press_r_to_view_whiteboard')
  }
}
