import { useState } from 'react'
import { Info } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

/** An (i) beside a label: the explanation opens on hover, or on tap where there is no hover. Its
 * target reaches past the icon, and a screen reader hears what it is about (`label`). */
export function InfoTip({ text, label }: { text?: string | null; label?: string }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        type="button"
        aria-label={label ? `About ${label}` : 'More information'}
        className="relative inline-flex rounded-sm text-muted-foreground after:absolute after:-inset-2.5 hover:text-foreground"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        <Info className="size-3.5" />
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-80 text-sm" onOpenAutoFocus={(e) => e.preventDefault()}>
        {text}
      </PopoverContent>
    </Popover>
  )
}
