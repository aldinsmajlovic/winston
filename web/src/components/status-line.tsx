import type { ReactNode } from 'react'
import { CircleAlert, CircleCheck, CircleX, Info, type LucideIcon } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type Tone = 'ok' | 'warning' | 'error' | 'muted'
type Action = { label: string; onClick: () => void } | { label: string; to: string }

const TONES: Record<Tone, { Icon: LucideIcon; className: string }> = {
  ok: { Icon: CircleCheck, className: 'text-success' },
  warning: { Icon: CircleAlert, className: 'text-warning' },
  error: { Icon: CircleX, className: 'text-destructive' },
  muted: { Icon: Info, className: 'text-muted-foreground' },
}

type Props = { tone: Tone; children: ReactNode; action?: Action | Action[] }

/** One fact as a sentence with an icon for its tone, and what to do about it as a labelled button
 * ("Hevy needs a key." [Open Hevy]). The reason is in the words, never only in a tooltip. */
export function StatusLine({ tone, children, action }: Props) {
  const { Icon, className } = TONES[tone]
  const actions = action ? [action].flat() : []
  return (
    <div role={tone === 'error' ? 'alert' : undefined} className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
      <span className="flex min-w-0 flex-1 basis-60 items-start gap-2">
        <Icon className={cn('mt-0.5 size-4 shrink-0', className)} />
        <span className="min-w-0">{children}</span>
      </span>
      {actions.map((a) =>
        'to' in a ? (
          <Button key={a.label} variant="outline" size="sm" asChild>
            <Link to={a.to}>{a.label}</Link>
          </Button>
        ) : (
          <Button key={a.label} variant="outline" size="sm" onClick={a.onClick}>
            {a.label}
          </Button>
        ),
      )}
    </div>
  )
}
