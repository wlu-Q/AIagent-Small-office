import { t, useLocale } from './i18n'
import React, { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { HashRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom'

import OfficeBootstrapScreen from './components/OfficeBootstrapScreen'
import { officeApi } from './services/office/officeApi'

const OfficeShell = lazy(() => import('./OfficeShell'))

function LoginRoute() {
  useLocale()
  const navigate = useNavigate()
  const navigateToOffice = useCallback(() => {
    navigate('/office', { replace: true })
  }, [navigate])

  return <OfficeBootstrapScreen onInitialized={navigateToOffice} />
}

function OfficeRoute() {
  useLocale()
  const [checking, setChecking] = useState(true)
  const [initialized, setInitialized] = useState(false)

  useEffect(() => {
    let active = true
    void officeApi.getBootstrap()
      .then((status) => {
        if (!active) return
        setInitialized(status.initialized)
        setChecking(false)
      })
      .catch(() => {
        if (!active) return
        setInitialized(false)
        setChecking(false)
      })
    return () => { active = false }
  }, [])

  if (checking) return <div aria-busy="true">{t('ui.loading_the_office')}</div>
  if (!initialized) return <Navigate to="/login" replace />

  return (
    <Suspense fallback={<div aria-busy="true">{t('ui.loading_the_office')}</div>}>
      <OfficeShell />
    </Suspense>
  )
}

export default function App() {
  useLocale()
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/office" element={<OfficeRoute />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </HashRouter>
  )
}
