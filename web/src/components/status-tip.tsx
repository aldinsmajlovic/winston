import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

/** A status as an icon and a colour, its words in the tooltip (one line per entry). */
export function StatusTip({ icon: Icon, className, label, lines }: { icon: LucideIcon; className: string; label: string; lines?: ReactNode[] }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex" tabIndex={0} role="img" aria-label={label}>
          <Icon className={cn('size-4', className)} />
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <div className="flex max-w-80 flex-col gap-0.5">
          <span>{label}</span>
          {(lines ?? []).map((l, i) => (
            <span key={i}>{l}</span>
          ))}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}
