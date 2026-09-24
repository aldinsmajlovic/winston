import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { STATE_LABEL, type PresenceState } from '@/api/live'
import { Presence } from '@/components/presence'
import { cn } from '@/lib/utils'

const LINKS = [
  { to: '/', label: 'Talk' },
  { to: '/memory', label: 'Memory' },
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/status', label: 'Settings' },
]

type Props = { name: string; state: PresenceState; actions?: ReactNode }

/** Winston's pages share this: his presence, his state, and plain text links to the others. */
export function WinstonHeader({ name, state, actions }: Props) {
  return (
    <header className="flex w-full max-w-3xl items-center gap-4 px-4 pt-8 pb-6">
      <Presence state={state} className="size-13" />
      <div className="flex flex-col">
        <h1 className="text-xl font-bold">{name}</h1>
        <span className="text-muted-foreground">{STATE_LABEL[state]}</span>
      </div>
      <nav className="ml-auto flex items-center gap-6">
        {actions}
        {LINKS.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            end
            className={({ isActive }) =>
              cn('hover:text-foreground', isActive ? 'font-medium text-foreground' : 'text-muted-foreground')
            }
          >
            {l.label}
          </NavLink>
        ))}
      </nav>
    </header>
  )
}
