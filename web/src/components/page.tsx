import { useSyncExternalStore, type ReactNode } from 'react'
import { useQueryClient, type Query } from '@tanstack/react-query'
import { CircleAlert, LoaderCircle, RotateCw } from 'lucide-react'
import { errorText } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'

type Props = { title: string; actions?: ReactNode; children: ReactNode }

export function Page({ title, actions, children }: Props) {
  return (
    <>
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        <SidebarTrigger />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-base font-medium">{title}</h1>
        <div className="ml-auto flex items-center gap-3">{actions}</div>
      </header>
      <main className="flex w-full max-w-4xl flex-col gap-6 p-4 md:p-6">{children}</main>
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
