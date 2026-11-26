import { useEffect, useRef, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { toast, type ExternalToast } from 'sonner'
import type { EngineState, EngineStatus } from '@/api/client'
import { engineLabel, restartCost, useStatus } from '@/api/status'
import { Progress } from '@/components/ui/progress'
import { capitalize } from '@/lib/format'

const LONG = 6000

/** What happens to his parts, as toasts on every page: an engine installing (with its bar), then starting,
 * stays up until it runs, then says so; anything that fails or stops answering says why. One toast per
 * part, each update replacing the last; one closed by hand stays closed until the part's state changes.
 * The state a page opens on toasts nothing but a part already busy: Status shows the rest. */
export function EngineToasts() {
  const navigate = useNavigate()
  const engines = useStatus().data?.engines
  const seen = useRef<Record<string, EngineState>>(undefined)
  const closed = useRef<Record<string, EngineState>>({})
  const installed = useRef(new Set<string>()) // starting right after its install
  useEffect(() => {
    if (!engines) return
    const was = seen.current
    const now = (seen.current = Object.fromEntries(engines.map((e) => [e.name, e.state])))
    for (const name of Object.keys(was ?? {})) if (!(name in now)) toast.dismiss(`engine:${name}`)
    const status = { label: 'Status', onClick: () => void navigate('/status') }
    for (const e of engines) {
      const prev = was?.[e.name]
      const What = capitalize(engineLabel(e))
      if (closed.current[e.name] !== e.state) delete closed.current[e.name]
      // Sonner merges an update into the toast it replaces, so every key is given each time.
      const show = (kind: 'loading' | 'success' | 'error' | 'warning', title: string, o: ExternalToast = {}) =>
        toast[kind](title, { id: `engine:${e.name}`, description: undefined, action: undefined, duration: LONG, closeButton: true, ...o })
      if (prev === 'installing' && e.state === 'starting') installed.current.add(e.name)
      else if (e.state !== 'starting') installed.current.delete(e.name)
      if (e.kind === 'engine' && (e.state === 'installing' || e.state === 'starting')) {
        if (closed.current[e.name]) continue
        // Closed by hand, not replaced: a dismiss by code comes after a newer status.
        const onDismiss = () => void (seen.current === now && (closed.current[e.name] = e.state))
        show('loading', busyTitle(e, installed.current.has(e.name)), { description: <Busy e={e} />, duration: Infinity, onDismiss })
      } else if (prev === undefined || prev === e.state) {
        continue
      } else if (e.state === 'healthy' && e.kind === 'engine' && (prev === 'installing' || prev === 'starting')) {
        show('success', prev === 'installing' ? `Installed ${engineLabel(e)}; it is running` : `${What} is running`, { closeButton: false })
      } else if (e.state === 'error') {
        show('error', `${What} failed`, { description: e.detail, duration: Infinity, action: status })
      } else if (e.state === 'unhealthy' && prev === 'healthy') {
        show('warning', `${What} is not responding`, { description: e.detail, action: status })
      } else if (prev === 'installing' || prev === 'starting') {
        toast.dismiss(`engine:${e.name}`) // stopped by hand
      }
    }
  }, [engines, navigate])
  return null
}

/** "Installing the brain, 42%", "Installed the brain, starting it" or "Starting the brain". */
function busyTitle(e: EngineStatus, installed = false): string {
  if (e.state === 'installing') return `Installing ${engineLabel(e)}${e.progress != null ? `, ${Math.floor(e.progress * 100)}%` : ''}`
  return installed ? `Installed ${engineLabel(e)}, starting it` : `Starting ${engineLabel(e)}`
}

/** What it is doing ("The model, 6.2 of 16.4 GB"), and the bar while the sizes are known. */
function Busy({ e }: { e: EngineStatus }): ReactNode {
  const words = e.detail || (e.state === 'starting' ? `It takes ${restartCost(e)}.` : '')
  return (
    <div className="flex flex-col gap-2">
      {words ? <span>{capitalize(words)}</span> : null}
      {e.progress != null ? (
        <Progress
          value={e.progress * 100}
          aria-label={busyTitle(e)}
          className="*:data-[slot=progress-indicator]:duration-1000 *:data-[slot=progress-indicator]:ease-linear"
        />
      ) : null}
    </div>
  )
}
