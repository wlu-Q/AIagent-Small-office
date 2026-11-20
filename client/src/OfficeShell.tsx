import React, { lazy, Suspense, useState, useEffect } from 'react'

import './PhaserGame'
import AppToast from './components/AppToast'
import DesktopGatewayBadge from './components/DesktopGatewayBadge'
import DesktopZoomController from './components/DesktopZoomController'
import OfficeChatPanel from './components/OfficeChatPanel'
import OfficeEmployeePanel from './components/OfficeEmployeePanel'
import WorkspacePanel from './components/WorkspacePanel'
import OfficeSettingsDialog, { OfficeSettingsLauncher } from './components/OfficeSettingsDialog'
import WorkstationPreview from './components/WorkstationPreview'

const OfficeLibraryDialog = lazy(() => import('./components/office-library/OfficeLibraryDialog'))

export default function OfficeShell() {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [libraryMounted, setLibraryMounted] = useState(false)
  const [librarySource, setLibrarySource] = useState<string>()
  useEffect(() => {
    const openSource = (event: Event) => { setLibrarySource((event as CustomEvent<string>).detail); setLibraryMounted(true); setLibraryOpen(true) }
    window.addEventListener('office-library:open', openSource)
    return () => window.removeEventListener('office-library:open', openSource)
  }, [])

  return (
    <>
      <DesktopZoomController />
      <DesktopGatewayBadge />
      <OfficeSettingsLauncher onClick={() => setSettingsOpen(true)} />
      <OfficeChatPanel />
      <OfficeEmployeePanel onOpenLibrary={() => { setLibrarySource(undefined); setLibraryMounted(true); setLibraryOpen(true) }} />
      <WorkspacePanel />
      <AppToast />
      <WorkstationPreview />
      <OfficeSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      {libraryMounted && <Suspense fallback={null}>
        <OfficeLibraryDialog source={librarySource} open={libraryOpen} onClose={() => setLibraryOpen(false)} />
      </Suspense>}
    </>
  )
}
