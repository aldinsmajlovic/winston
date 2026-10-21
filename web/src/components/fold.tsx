import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** A fold's toggle: its text lines up with the column, and an open fold does not look pressed. */
const FOLD = '-ml-3 text-muted-foreground aria-expanded:bg-transparent aria-expanded:text-muted-foreground'

type Props = {
  title: string
  icon?: ReactNode
  /** Muted words beside the title ("2 of 5 shared", "Docker container"). */
  summary?: string
  /** Where it lives, as a Settings link at the right. */
  to?: string
  /** Draws it open and keeps it so (an error from a refused save under it); it stays open after. */
  forceOpen?: boolean
  children: ReactNode
}

/** A group that folds, closed at first: its icon and name, a summary beside it, and a link to where it lives. */
export function Fold({ title, icon, summary, to, forceOpen, children }: Props) {
  const [open, setOpen] = useState(false)
  // Opened in the same render the error arrives in, so the scroll to it finds the field.
  if (forceOpen && !open) setOpen(true)
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Button type="button" variant="ghost" className={cn(FOLD, 'justify-start')} aria-expanded={open} onClick={() => setOpen(!open || !!forceOpen)}>
          <ChevronRight className={cn('transition-transform', open && 'rotate-90')} />
          {icon}
          <span className="font-medium text-foreground">{title}</span>
        </Button>
        {summary ? <span className="text-sm text-muted-foreground">{summary}</span> : null}
        {to ? (
          <Button asChild variant="link" size="sm" className="ml-auto px-0">
            <Link to={to} aria-label={`Settings for ${title}`}>
              Settings
            </Link>
          </Button>
        ) : null}
      </div>
      {open ? children : null}
    </div>
  )
}
