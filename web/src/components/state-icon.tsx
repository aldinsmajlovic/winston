import { Circle, CircleAlert, CircleCheck, CircleX, LoaderCircle, RefreshCw } from 'lucide-react'
import type { EngineState } from '@/api/client'
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

export function ChangedIcon() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">
          <RefreshCw className="size-4 text-warning" aria-label="restart to apply" />
        </span>
      </TooltipTrigger>
      <TooltipContent>Saved config waits for a restart</TooltipContent>
    </Tooltip>
  )
}
