import { useEffect, useSyncExternalStore, type ReactNode } from 'react'
import { useQueryClient, type Query } from '@tanstack/react-query'
import { CircleAlert, EllipsisVertical, LoaderCircle, RotateCw, type LucideIcon } from 'lucide-react'
import { Link } from 'react-router'
import { $api, errorText } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/** The tab's title: "<page> · <his name>", or his name alone. */
// eslint-disable-next-line react-refresh/only-export-components -- the hook every page sets its title with, beside Page
export function useTitle(title?: string) {
  const name = $api.useQuery('get', '/api/status').data?.assistant
  useEffect(() => {
    const text = [title, name].filter(Boolean).join(' · ')
    if (text) document.title = text
  }, [title, name])
}

/** Something the page does now (Start all, Add an MCP server), as opposed to a setting that waits for Save. */
type Command = { label: string; icon?: LucideIcon; onClick: () => void; disabled?: boolean }

/** Text buttons; in a narrow header two or more fold into one More menu, so the title keeps its room.
 * One stays a button, since a menu of one saves no room. Its items are words, as in every menu. */
function Commands({ commands }: { commands: Command[] }) {
  const fold = commands.length > 1
  return (
    <>
      {commands.map(({ label, icon: Icon, onClick, disabled }) => (
        <Button key={label} variant="outline" size="sm" className={cn(fold && '@max-4xl/header:hidden')} disabled={disabled} onClick={onClick}>
          {Icon ? <Icon data-icon="inline-start" /> : null}
          {label}
        </Button>
      ))}
      {fold ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton label="More" className="@4xl/header:hidden">
              <EllipsisVertical />
            </IconButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {commands.map(({ label, onClick, disabled }) => (
              <DropdownMenuItem key={label} disabled={disabled} onSelect={onClick}>
                {label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </>
  )
}

type Props = {
  title: string
  /** The page above this one, drawn as a breadcrumb before the title ("Extensions / Spotify"). */
  back?: { to: string; label: string }
  /** One sentence on what the page is for, first in it. */
  intro?: ReactNode
  commands?: Command[]
  /** At the right of the header, after the commands: the save controls. */
  actions?: ReactNode
  /** A bar fixed along the bottom on a phone (unsaved changes); the page leaves room for it. */
  footer?: ReactNode
  children?: ReactNode
}

export function Page({ title, back, intro, commands, actions, footer, children }: Props) {
  useTitle(title)
  return (
    <>
      {/* Laid out by its own width, not the window's, since the sidebar takes part of it. As it narrows the
          restart words shorten first, then the commands fold into More and the breadcrumb goes; in a phone's
          width the words go and Restart becomes its icon, so the title keeps its room and nothing scrolls
          sideways. */}
      <header className="@container/header flex h-14 shrink-0 items-center gap-2 border-b px-4">
        <SidebarTrigger />
        <Separator orientation="vertical" className="mr-2 data-vertical:h-4 data-vertical:self-center" />
        {back ? (
          <span className="flex shrink-0 items-center gap-2 text-muted-foreground @max-2xl/header:hidden">
            <Link to={back.to} className="hover:text-foreground">
              {back.label}
            </Link>
            <span aria-hidden>/</span>
          </span>
        ) : null}
        <h1 className="mr-auto min-w-0 truncate text-base font-medium">{title}</h1>
        {commands?.length ? <Commands commands={commands} /> : null}
        {actions}
      </header>
      <main className={cn('flex w-full max-w-4xl flex-col gap-6 p-4 md:p-6', footer && 'max-sm:pb-20')}>
        {intro ? <p className="text-muted-foreground">{intro}</p> : null}
        {children}
      </main>
      {footer}
    </>
  )
}

type LoadErrorProps = { error: unknown; retrying?: boolean; onRetry: () => void }

/** What a page could not load, in place of its content: an icon, the reason, and Retry. */
export function LoadError({ error, retrying, onRetry }: LoadErrorProps) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3">
      <CircleAlert className="size-4 shrink-0 text-destructive" />
      <span className="text-sm">{errorText(error)}</span>
      <Button variant="outline" size="sm" disabled={retrying} onClick={onRetry}>
        {retrying ? <LoaderCircle className="animate-spin" /> : <RotateCw />}
        Retry
      </Button>
    </div>
  )
}

// A query the page is waiting on that failed: someone observes it, it has no data, its last fetch errored.
const blocking = (q: Query) => q.state.status === 'error' && q.state.data === undefined && q.getObserversCount() > 0
// One still on its first fetch: the page may yet load, so the skeleton stays until it settles.
const loading = (q: Query) => q.state.status === 'pending' && q.state.fetchStatus === 'fetching' && q.getObserversCount() > 0

/** The queries a page is stuck on, e.g. the config or the presets; empty while all is well or loading. */
function useFailedQueries() {
  const client = useQueryClient()
  const cache = client.getQueryCache()
  // A string snapshot, so the store only changes when the set or its fetching does.
  const key = useSyncExternalStore(
    (notify) => cache.subscribe(notify),
    () =>
      cache
        .findAll({ predicate: blocking })
        .map((q) => `${q.queryHash}\t${q.state.fetchStatus}`)
        .join('\n') + (cache.findAll({ predicate: loading }).length ? '\nloading' : ''),
  )
  const failed = key && !key.endsWith('\nloading') ? cache.findAll({ predicate: blocking }) : []
  return {
    error: failed[0]?.state.error,
    retrying: failed.some((q) => q.state.fetchStatus === 'fetching'),
    retry: () => void client.refetchQueries({ predicate: blocking }),
  }
}

/** A page still loading; if what it waits on failed, the reason and Retry instead. */
export function PageSkeleton({ title }: { title: string }) {
  const failed = useFailedQueries()
  return (
    <Page title={title}>
      {failed.error !== undefined ? (
        <LoadError error={failed.error} retrying={failed.retrying} onRetry={failed.retry} />
      ) : (
        <>
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-2/3" />
        </>
      )}
    </Page>
  )
}
