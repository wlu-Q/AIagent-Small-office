import { useEffect } from 'react'

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  const tagName = target.tagName.toLowerCase()
  return tagName === 'input' || tagName === 'textarea' || target.isContentEditable
}

export default function DesktopZoomController() {
  useEffect(() => {
    const bridge = window.officeDesktopBridge
    if (!bridge) {
      return
    }

    const handleWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) {
        return
      }

      event.preventDefault()
      void bridge.zoomBy(event.deltaY < 0 ? 0.1 : -0.1)
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) {
        return
      }

      if (isEditableTarget(event.target)) {
        return
      }

      if (event.key === '0') {
        event.preventDefault()
        void bridge.resetZoom()
        return
      }

      if (event.key === '=' || event.key === '+') {
        event.preventDefault()
        void bridge.zoomBy(0.1)
        return
      }

      if (event.key === '-') {
        event.preventDefault()
        void bridge.zoomBy(-0.1)
      }
    }

    window.addEventListener('wheel', handleWheel, { passive: false })
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('wheel', handleWheel)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  return null
}
