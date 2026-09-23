import { useState } from 'react'
import { Info } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

/** An (i) beside a label: the explanation opens on hover, or on tap where there is no hover. */
export function InfoTip({ text }: { text?: string | null }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        type="button"
        aria-label="More information"
        className="inline-flex text-muted-foreground hover:text-foreground"
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
