import { useEffect, useRef, useState } from 'react'
import { officeApi, type OfficeGroupExecution } from '../../services/office/officeApi'

export function useGroupExecution(enabled: boolean) {
  const [state, setState] = useState<OfficeGroupExecution | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const controlling = useRef(false)
  const revision = useRef(0)
  useEffect(() => {
    if (!enabled) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      const version = revision.current
      try {
        if (controlling.current) return
        const next = await officeApi.getGroupExecution()
        if (active && version === revision.current) { setState(next); setError('') }
      } catch (e) { if (active && version === revision.current) setError(String(e)) }
      finally { if (active) timer = setTimeout(refresh, 1000) }
    }
    void refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [enabled])
  const pause = async () => {
    if (controlling.current) return
    controlling.current = true
    revision.current += 1
    setBusy(true)
    try { setState(await officeApi.pauseGroup()); setError('') }
    catch (e) { setError(String(e)); throw e }
    finally { revision.current += 1; controlling.current = false; setBusy(false) }
  }
  return { state: enabled ? state : null, error: enabled ? error : '', busy, pause }
}
