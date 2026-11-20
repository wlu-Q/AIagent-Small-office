import manifest from '../../public/assets/workstations/manifest.json'
import type { WorkstationHandStyle } from './workstationModel'

export { manifest as workstationArtworkManifest }
export type WorkstationArtwork = {
  base: HTMLImageElement
  props: HTMLImageElement
  screens: HTMLImageElement
  wallpaper: HTMLImageElement
  hands?: HTMLImageElement
  handStyle?: WorkstationHandStyle
}

const images = new Map<string, Promise<HTMLImageElement>>()

function load(filename: string) {
  const cached = images.get(filename)
  if (cached) return cached
  const pending = new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error(`Workstation image failed: ${filename}`))
    image.src = `assets/workstations/${filename}.png`
  }).catch(error => { images.delete(filename); throw error })
  images.set(filename, pending)
  return pending
}

export async function loadWorkstationArtwork(handStyle?: WorkstationHandStyle): Promise<WorkstationArtwork> {
  const [base, props, screens, wallpaper, hands] = await Promise.all([
    load('desk-base'), load('props'), load('screen-frames'), load('desktop-wallpaper'),
    handStyle ? load(manifest.hands[handStyle].file) : undefined,
  ])
  return { base, props, screens, wallpaper, hands, handStyle }
}
