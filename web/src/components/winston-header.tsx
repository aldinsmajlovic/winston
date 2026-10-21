import type { ReactNode } from 'react'
import { CircleAlert, History, NotebookText, Settings } from 'lucide-react'
import { Link, useLocation } from 'react-router'
import { useConfig } from '@/api/config'
import { notReady, STATE_LABEL, type PresenceState } from '@/api/live'
import { needsAttention, useStatus } from '@/api/status'
import { Presence } from '@/components/presence'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const LINKS = [
  { to: '/history', label: 'History', Icon: History },
  { to: '/memory', label: 'Memory', Icon: NotebookText },
  { to: '/status', label: 'Settings', Icon: Settings },
]

type Props = { name: string; state: PresenceState; actions?: ReactNode }

/** Winston's pages share this: his presence, name and whether he can answer, which lead back to Talk, and
 * a link with its name for each other page. Settings carries a mark while something needs you. */
export function WinstonHeader({ name, state, actions }: Props) {
  const { pathname } = useLocation()
  const status = useStatus()
  const config = useConfig()
  const problem = notReady(status.data, config.data, status.isError)
  const attention = needsAttention(status.data)
  return (
    <header className="flex w-full max-w-3xl items-center gap-3 px-4 pt-8 pb-6 sm:gap-4">
      {/* The name's link covers his presence too; the Status link sits above it. */}
      <div className="relative flex min-w-0 items-center gap-3 sm:gap-4">
        <Presence state={state} className="size-11 shrink-0 sm:size-13" />
        <div className="flex min-w-0 flex-col">
          <h1 className="text-xl font-bold break-words">
            <Link to="/" aria-label={`Talk to ${name}`} className="after:absolute after:inset-0">
              {name}
            </Link>
          </h1>
          <span className="text-muted-foreground max-sm:text-sm">
            {problem ? problem.text : STATE_LABEL[state]}
            {problem && !problem.starting ? (
              <>
                {' '}
                <Link to="/status" className="relative text-foreground underline underline-offset-4">
                  Status
                </Link>
              </>
            ) : null}
          </span>
        </div>
      </div>
      <nav className="ml-auto flex shrink-0 items-center gap-1">
        {actions}
        {LINKS.map(({ to, label, Icon }) => (
          <Button
            key={to}
            variant="ghost"
            asChild
            className={cn(
              'h-auto gap-0.5 px-1.5 py-1.5 text-xs hover:text-foreground max-sm:flex-col sm:gap-2 sm:px-3 sm:text-sm',
              pathname === to ? 'bg-accent text-foreground' : 'text-muted-foreground',
            )}
          >
            <Link to={to} aria-current={pathname === to ? 'page' : undefined}>
              <span className="relative">
                <Icon className="size-5" />
                {to === '/status' && attention ? (
                  <CircleAlert
                    role="img"
                    aria-label="Needs attention"
                    className="absolute -top-1 -right-1.5 size-3 rounded-full bg-background text-warning"
                  />
                ) : null}
              </span>
              {label}
            </Link>
          </Button>
        ))}
      </nav>
    </header>
  )
}
