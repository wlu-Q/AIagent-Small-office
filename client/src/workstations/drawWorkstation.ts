import { handPose, SCREEN_RECT, WORKSTATION_WIDTH, WORKSTATION_HEIGHT, type DeskLayout } from './workstationModel'
import { workstationArtworkManifest as manifest, type WorkstationArtwork } from './workstationArtwork'

export function drawWorkstation(
  context: CanvasRenderingContext2D,
  artwork: WorkstationArtwork,
  layout: DeskLayout,
  occupied: boolean,
  operating: boolean,
  elapsed: number,
) {
  context.imageSmoothingEnabled = false
  context.clearRect(0, 0, WORKSTATION_WIDTH, WORKSTATION_HEIGHT)
  context.drawImage(artwork.base, 0, 0)
  if (!occupied) {
    context.fillStyle = '#000000'
    context.fillRect(SCREEN_RECT.x, SCREEN_RECT.y, SCREEN_RECT.width, SCREEN_RECT.height)
  } else {
    context.drawImage(artwork.wallpaper, SCREEN_RECT.x, SCREEN_RECT.y, SCREEN_RECT.width, SCREEN_RECT.height)
  }
  const sprite = (name: keyof typeof manifest.props, centerX: number, bottom: number, height: number) => {
    const source = manifest.props[name]
    const width = Math.round(source.width / source.height * height)
    context.drawImage(artwork.props, source.x, source.y, source.width, source.height,
      Math.round(centerX - width / 2), Math.round(bottom - height), width, height)
  }
  if (layout.tower) sprite('tower', 1450, 650, 400)
  if (occupied) {
    const [decoration, drink, accessory] = layout.props
    sprite(decoration, 222, 636, decoration === 'cactus' ? 230 : 300)
    sprite(drink, 175, 824, drink === 'water' ? 180 : drink === 'lemonTea' ? 140 : 160)
    const height = accessory === 'tissueBox' ? 160 : accessory === 'deskClock' ? 180 : 185
    sprite(accessory, layout.tower ? 1544 : 1440, layout.tower ? 860 : 650, height)
  }
  const pose = handPose(elapsed)
  const mouseX = operating ? pose.mouseX * 3 : 0
  const mouseY = operating ? pose.mouseY * 3 : 0
  sprite('mouse', 1220 + mouseX, 819 + mouseY, 128)
  if (!operating) return
  context.drawImage(artwork.screens, 0, 0, manifest.screenFrames.width, manifest.screenFrames.height,
    SCREEN_RECT.x, SCREEN_RECT.y, SCREEN_RECT.width, SCREEN_RECT.height)
  if (!artwork.hands || !artwork.handStyle) return
  const hands = artwork.hands
  const atlas = manifest.hands[artwork.handStyle]
  const hand = (name: keyof typeof atlas.frames, wristX: number, bottom: number) => {
    const source = atlas.frames[name]
    const scale = atlas.scale
    context.drawImage(hands, source.x, source.y, source.width, source.height,
      Math.round(wristX - source.anchorX * scale), Math.round(bottom - source.anchorY * scale),
      Math.round(source.width * scale), Math.round(source.height * scale))
  }
  hand(`left${pose.leftFrame}` as keyof typeof atlas.frames, 560 + pose.leftX * 3, 980 + pose.leftY * 3)
  hand(`right${pose.rightFrame}` as keyof typeof atlas.frames, 1270 + pose.rightX * 3, 980 + pose.rightY * 3)
}
