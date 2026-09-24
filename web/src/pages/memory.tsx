import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { $api, errorText, type MemoryFact, type MemoryTopic } from '@/api/client'
import { presence, useLive } from '@/api/live'
import { IconButton } from '@/components/icon-button'
import { WinstonHeader } from '@/components/winston-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

const SAVED = 'Saved. He knows it from his next conversation.'

/** "2026-09-24" to "24 Sep", or "Sep 24" where the region puts the month first. */
function noted(date: string | null | undefined, dayFirst: boolean): string {
  if (!date) return ''
  const d = new Date(`${date}T12:00:00`)
  const day = d.getDate()
  const month = d.toLocaleDateString('en', { month: 'short' })
  return dayFirst ? `${day} ${month}` : `${month} ${day}`
}

const matches = (topic: MemoryTopic, fact: MemoryFact, query: string) =>
  !query || fact.text.toLowerCase().includes(query) || topic.title.toLowerCase().includes(query)

/** The fact with each occurrence of the search marked. */
function Marked({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>
  const parts: ReactNode[] = []
  const lower = text.toLowerCase()
  let from = 0
  for (let at = lower.indexOf(query); at >= 0; at = lower.indexOf(query, from)) {
    parts.push(text.slice(from, at))
    parts.push(
      <mark key={at} className="rounded-sm bg-border px-0.5 text-inherit">
        {text.slice(at, at + query.length)}
      </mark>,
    )
    from = at + query.length
  }
  parts.push(text.slice(from))
  return <>{parts}</>
}

/** What the page can do to memory. add and edit return why they were refused, or null. */
type Actions = {
  add: (topic: string, fact: string) => Promise<string | null>
  edit: (topic: string, id: string, fact: string) => Promise<string | null>
  forget: (topic: string, fact: MemoryFact) => void
}

type RowProps = { topic: string; fact: MemoryFact; query: string; dayFirst: boolean; act: Actions }

function FactRow({ topic, fact, query, dayFirst, act }: RowProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const close = () => {
    setDraft(null)
    setError(null)
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    const text = draft?.trim()
    if (!text || text === fact.text) return close()
    const why = await act.edit(topic, fact.id, text)
    if (why) setError(why)
    else close()
  }
  if (draft !== null) {
    return (
      <form onSubmit={save} className="-mx-3 flex flex-col gap-2 rounded-lg bg-card px-3 py-2.5">
        <Input
          autoFocus
          aria-label="Edit fact"
          aria-invalid={Boolean(error)}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            setError(null)
          }}
          onKeyDown={(e) => e.key === 'Escape' && close()}
        />
        {error ? <span className="text-sm text-destructive">{error}</span> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit">Save</Button>
        </div>
      </form>
    )
  }
  return (
    <div className="group -mx-3 flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-card">
      <span className="flex-1">
        <Marked text={fact.text} query={query} />
      </span>
      <span className="flex opacity-0 group-focus-within:opacity-100 group-hover:opacity-100">
        <IconButton label="Edit" onClick={() => setDraft(fact.text)}>
          <Pencil />
        </IconButton>
        <IconButton label="Forget" onClick={() => act.forget(topic, fact)}>
          <Trash2 />
        </IconButton>
      </span>
      <span className="shrink-0 text-muted-foreground">{noted(fact.noted, dayFirst)}</span>
    </div>
  )
}

/** A rounded field like the Talk page's. Enter adds; a refusal shows under it and the text stays. */
function AddField({ topic, act }: { topic: MemoryTopic; act: Actions }) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!text.trim()) return
    const why = await act.add(topic.name, text.trim())
    setError(why)
    if (!why) setText('')
  }
  const label = `Add to ${topic.title.toLowerCase()}`
  return (
    <form onSubmit={submit} className="mt-1 flex flex-col gap-1">
      <label
        className={cn(
          'flex h-10 items-center gap-2.5 rounded-full bg-card px-4 text-muted-foreground',
          error && 'ring-1 ring-destructive',
        )}
      >
        <Plus className="size-4 shrink-0" />
        <input
          aria-label={label}
          aria-invalid={Boolean(error)}
          placeholder={label}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setError(null)
          }}
          className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
        />
      </label>
      {error ? <span className="px-4 text-sm text-destructive">{error}</span> : null}
    </form>
  )
}

type TopicProps = { topic: MemoryTopic; query: string; dayFirst: boolean; act: Actions }

function Topic({ topic, query, dayFirst, act }: TopicProps) {
  const facts = topic.facts.filter((f) => matches(topic, f, query))
  if (query && facts.length === 0) return null
  return (
    <section aria-label={topic.title} className="flex flex-col gap-0.5">
      <h2 className="mb-1.5 text-lg font-semibold">{topic.title}</h2>
      {facts.map((f) => (
        <FactRow key={f.id} topic={topic.name} fact={f} query={query} dayFirst={dayFirst} act={act} />
      ))}
      {query ? null : (
        <>
          {facts.length === 0 ? <span className="py-2 text-muted-foreground">Nothing yet.</span> : null}
          <AddField topic={topic} act={act} />
        </>
      )}
    </section>
  )
}

function NewTopic({ onCreate }: { onCreate: (title: string) => Promise<boolean> }) {
  const [name, setName] = useState<string | null>(null)
  if (name === null) {
    return (
      <Button type="button" variant="ghost" className="h-11 text-muted-foreground" onClick={() => setName('')}>
        <Plus />
        New topic
      </Button>
    )
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim() || (await onCreate(name.trim()))) setName(null)
  }
  return (
    <form onSubmit={submit}>
      <Input
        autoFocus
        aria-label="Topic name"
        placeholder="Topic name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && setName(null)}
        onBlur={() => !name.trim() && setName(null)}
        className="h-11 w-48 rounded-full"
      />
    </form>
  )
}

/** Template: Winston's page. What he remembers, read at the start of every conversation: one section
 * per topic file in data/memory, facts as stored, search across them, add, edit and forget in place. */
export default function MemoryPage() {
  const feed = useLive()
  const status = $api.useQuery('get', '/api/status')
  const memory = $api.useQuery('get', '/api/memory')
  const add = $api.useMutation('post', '/api/memory/{topic}')
  const edit = $api.useMutation('put', '/api/memory/{topic}')
  const forget = $api.useMutation('delete', '/api/memory/{topic}')
  const addTopic = $api.useMutation('post', '/api/memory/topics')
  const [search, setSearch] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const query = search.trim().toLowerCase()

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 10_000)
    return () => clearInterval(timer)
  }, [])

  const refresh = () => void memory.refetch()
  const act: Actions = {
    add: async (topic, fact) => {
      try {
        await add.mutateAsync({ params: { path: { topic } }, body: { fact } })
      } catch (error) {
        return errorText(error)
      }
      refresh()
      toast.success(SAVED)
      return null
    },
    edit: async (topic, id, fact) => {
      try {
        await edit.mutateAsync({
          params: { path: { topic } },
          body: { id, fact },
        })
      } catch (error) {
        return errorText(error)
      }
      refresh()
      toast.success(SAVED)
      return null
    },
    forget: (topic, fact) =>
      forget.mutate(
        { params: { path: { topic }, query: { id: fact.id } } },
        {
          onError: (error) => toast.error(errorText(error)),
          onSuccess: ({ index }) => {
            refresh()
            toast(`Forgot: ${fact.text}`, {
              action: {
                label: 'Undo',
                onClick: () =>
                  add.mutate(
                    {
                      params: { path: { topic } },
                      body: { fact: fact.text, index, noted: fact.noted },
                    },
                    {
                      onSuccess: refresh,
                      onError: (error) => toast.error(errorText(error)),
                    },
                  ),
              },
            })
          },
        },
      ),
  }
  const createTopic = async (title: string) => {
    try {
      await addTopic.mutateAsync({ body: { title } })
    } catch (error) {
      toast.error(errorText(error))
      return false
    }
    refresh()
    return true
  }

  const data = memory.data
  const total = data?.topics.reduce((n, t) => n + t.facts.length, 0) ?? 0
  const shown = data?.topics.reduce((n, t) => n + t.facts.filter((f) => matches(t, f, query)).length, 0) ?? 0

  return (
    <div className="flex min-h-svh flex-col items-center">
      <WinstonHeader name={status.data?.assistant ?? 'Winston'} state={presence(feed, now)} />
      <main className="flex w-full max-w-3xl flex-1 flex-col gap-7 px-4 pb-8">
        <div className="flex items-center gap-3">
          <label className="flex h-11 flex-1 items-center gap-2.5 rounded-full bg-card px-4 text-muted-foreground">
            <Search className="size-4 shrink-0" />
            <input
              role="searchbox"
              aria-label="Search memory"
              placeholder="Search what he remembers"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
            />
            {search ? (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => setSearch('')}
                className="hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            ) : null}
          </label>
          <NewTopic onCreate={createTopic} />
        </div>

        {!data ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-6 w-32" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : (
          <>
            {query ? <span className="-mt-3 text-muted-foreground">{`${shown} of ${total} facts`}</span> : null}
            {data.topics.map((t) => (
              <Topic key={t.name} topic={t} query={query} dayFirst={data.day_first} act={act} />
            ))}
            {data.chars > 0 && !query ? (
              <div className="mt-auto flex items-center gap-3 pt-4 text-sm text-muted-foreground">
                <Progress
                  aria-label="Memory in every conversation"
                  value={Math.min(100, (data.chars / data.limit) * 100)}
                  className="w-30"
                />
                <span>{`${total} facts, ${data.chars.toLocaleString()} of ${data.limit.toLocaleString()} characters`}</span>
              </div>
            ) : null}
          </>
        )}
      </main>
    </div>
  )
}
