import { Circle, CircleAlert, CircleCheck, CircleX, LoaderCircle, RefreshCw } from 'lucide-react'
import type { EngineState } from '@/api/client'
import { KindIcon } from '@/components/kind-icon'
import type { DriverChoice } from '@/hooks/use-drivers'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

// A status is an icon plus a colour (docs/ui-guide.md).
const states: Record<EngineState, { Icon: typeof Circle; className: string }> = {
  healthy: { Icon: CircleCheck, className: 'text-success' },
  starting: { Icon: LoaderCircle, className: 'animate-spin text-muted-foreground' },
  unhealthy: { Icon: CircleAlert, className: 'text-warning' },
  stopped: { Icon: Circle, className: 'text-muted-foreground' },
  error: { Icon: CircleX, className: 'text-destructive' },
}

export function StateIcon({ state, detail }: { state: EngineState; detail?: string }) {
  const { Icon, className } = states[state]
  const icon = <Icon className={cn('size-4', className)} aria-label={state} />
  const text = detail || state
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{icon}</span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  )
}

export function ChangedIcon({ text = 'Saved config waits for a restart' }: { text?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex" tabIndex={0}>
          <RefreshCw className="size-4 text-warning" aria-label={text} />
        </span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
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
