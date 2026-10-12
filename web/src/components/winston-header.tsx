import type { ReactNode } from 'react'
import { History, Monitor, NotebookText, Settings } from 'lucide-react'
import { Link, useLocation } from 'react-router'
import { STATE_LABEL, type PresenceState } from '@/api/live'
import { Presence } from '@/components/presence'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

const LINKS = [
  { to: '/turns', label: 'Turns and diary', Icon: History },
  { to: '/memory', label: 'Memory', Icon: NotebookText },
  { to: '/dashboard', label: 'Dashboard', Icon: Monitor },
  { to: '/system', label: 'Settings', Icon: Settings },
]

type Props = { name: string; state: PresenceState; actions?: ReactNode }

/** Winston's pages share this: his presence and state, which lead back to Talk, and an icon for each other page. */
export function WinstonHeader({ name, state, actions }: Props) {
  const { pathname } = useLocation()
  return (
    <header className="flex w-full max-w-3xl items-center gap-4 px-4 pt-8 pb-6">
      <Link to="/" aria-label={`Talk to ${name}`} className="flex items-center gap-4">
        <Presence state={state} className="size-13" />
        <div className="flex flex-col">
          <h1 className="text-xl font-bold">{name}</h1>
          <span className="text-muted-foreground">{STATE_LABEL[state]}</span>
        </div>
      </Link>
      <nav className="ml-auto flex items-center gap-1">
        {actions}
        {LINKS.map(({ to, label, Icon }) => (
          <Tooltip key={to}>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                asChild
                className={cn('rounded-full hover:text-foreground', pathname === to ? 'bg-accent text-foreground' : 'text-muted-foreground')}
              >
                <Link to={to} aria-label={label} aria-current={pathname === to ? 'page' : undefined}>
                  <Icon className="size-5" />
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{label}</TooltipContent>
          </Tooltip>
        ))}
      </nav>
    </header>
  )
}
