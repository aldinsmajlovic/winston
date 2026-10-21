import { Circle, CircleAlert, CircleCheck, CircleX, LoaderCircle } from 'lucide-react'
import type { EngineState } from '@/api/client'
import { STATE_WORDS } from '@/api/status'
import { KindIcon } from '@/components/kind-icon'
import { StatusTip } from '@/components/status-tip'
import type { DriverChoice } from '@/hooks/use-drivers'

// A status is an icon plus a colour, and its word wherever it is not Running (docs/ui-guide.md).
const states: Record<EngineState, { Icon: typeof Circle; className: string }> = {
  healthy: { Icon: CircleCheck, className: 'text-success' },
  starting: { Icon: LoaderCircle, className: 'animate-spin text-muted-foreground' },
  unhealthy: { Icon: CircleAlert, className: 'text-warning' },
  stopped: { Icon: Circle, className: 'text-muted-foreground' },
  error: { Icon: CircleX, className: 'text-destructive' },
}

type Props = {
  state: EngineState
  detail?: string
  /** When its word shows beside the icon: never, whenever it is not Running (the default), or always. */
  words?: 'never' | 'problems' | 'always'
  /** Its own word in place of the state's ("Connected" for a service that answers). */
  word?: string
}

/** A state as its icon, and its word beside it unless the place says otherwise; the detail opens on tap. */
export function StateIcon({ state, detail, words = 'problems', word = STATE_WORDS[state] }: Props) {
  const { Icon, className } = states[state]
  const shown = words === 'always' || (words === 'problems' && state !== 'healthy')
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusTip icon={Icon} className={className} label={detail ? `${word}: ${detail}` : word} />
      {shown ? <span className="text-sm font-normal">{word}</span> : null}
    </span>
  )
}

/** How an engine is run, with the icon and label the config schema gives it. */
export function DriverLabel({ driver, drivers }: { driver: string; drivers: DriverChoice[] }) {
  const d = drivers.find((x) => x.value === driver)
  return (
    <span className="flex items-center gap-2">
      <KindIcon name={d?.icon} className="text-muted-foreground" />
      {d?.label ?? driver}
    </span>
  )
}
