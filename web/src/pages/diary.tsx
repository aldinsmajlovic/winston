import { $api } from '@/api/client'
import { presence, useLive } from '@/api/live'
import { LoadError } from '@/components/page'
import { WinstonHeader } from '@/components/winston-header'
import { Skeleton } from '@/components/ui/skeleton'
import { useNow } from '@/hooks/use-now'

function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'long',
  })
}

export default function DiaryPage() {
  const feed = useLive()
  const status = $api.useQuery('get', '/api/status')
  const diary = $api.useQuery('get', '/api/diary', { params: { query: { limit: 30 } } })
  const now = useNow(10_000)
  const entries = diary.data ?? []

  return (
    <div className="flex min-h-svh flex-col items-center">
      <WinstonHeader name={status.data?.assistant ?? 'Winston'} state={presence(feed, now)} />
      <main className="flex w-full max-w-3xl flex-1 flex-col gap-8 px-4 pb-8">
        {!diary.data && diary.isError ? (
          <LoadError error={diary.error} retrying={diary.isFetching} onRetry={() => void diary.refetch()} />
        ) : !diary.data ? (
          <div className="flex flex-col gap-4">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : entries.length === 0 ? (
          <p className="text-muted-foreground">No diary entries yet.</p>
        ) : (
          entries.map((entry) => (
            <article key={entry.date} className="flex flex-col gap-2">
              <h2 className="font-medium text-muted-foreground">{dayLabel(entry.date)}</h2>
              <p className="text-lg/relaxed whitespace-pre-wrap">{entry.text}</p>
            </article>
          ))
        )}
      </main>
    </div>
  )
}
