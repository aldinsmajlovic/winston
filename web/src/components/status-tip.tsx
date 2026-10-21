import { useState, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

/** A status as an icon and a colour; its words open on hover, or on tap where there is no hover
 * (one line per entry). */
export function StatusTip({ icon: Icon, className, label, lines }: { icon: LucideIcon; className: string; label: string; lines?: ReactNode[] }) {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        type="button"
        aria-label={label}
        className="relative inline-flex after:absolute after:-inset-2.5"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        <Icon className={cn('size-4', className)} />
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="flex w-auto max-w-80 flex-col gap-0.5 text-sm" onOpenAutoFocus={(e) => e.preventDefault()}>
        <span>{label}</span>
        {(lines ?? []).map((l, i) => (
          <span key={i}>{l}</span>
        ))}
      </PopoverContent>
    </Popover>
  )
}
