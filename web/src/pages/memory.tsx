import { useEffect, useState, type ClipboardEvent, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ChevronRight, ListCollapse, Lock, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { $api, errorText, type MemoryFact, type MemoryTopic } from '@/api/client'
import { presence, useLive } from '@/api/live'
import { IconButton } from '@/components/icon-button'
import { LoadError } from '@/components/page'
import { WinstonHeader } from '@/components/winston-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useConfirm } from '@/hooks/use-confirm'
import { cn } from '@/lib/utils'

const SAVED = 'Saved. He knows it from his next conversation.'
const SAVED_OFF = 'Saved. He does not use it while memory is off.'
// Every action here shows its own errors, in place or as a toast.
const OWN_ERRORS = { meta: { showsErrors: true } }

/** What a save returns: why it was refused, or null. */
type Result = Promise<string | null>

async function attempt(run: () => Promise<unknown>): Result {
  try {
    await run()
    return null
  } catch (error) {
    return errorText(error)
  }
}

/** "2026-09-24" to "24 Sep", or "Sep 24" where the region puts the month first. */
function noted(date: string | null | undefined, dayFirst: boolean): string {
  if (!date) return ''
  const d = new Date(`${date}T12:00:00`)
  const month = d.toLocaleDateString('en', { month: 'short' })
  return dayFirst ? `${d.getDate()} ${month}` : `${month} ${d.getDate()}`
}

const has = (text: string, query: string) => !query || text.toLowerCase().includes(query)

/** The text with each occurrence of the search marked. */
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

type RowProps = {
  text: string
  date?: string
  query: string
  removeLabel: string
  muted?: boolean
  onEdit: (text: string) => Result
  onRemove: () => void
}

/** One fact or rule: plain text, edit and remove on hover, edited in place. */
function LineRow({ text, date, query, removeLabel, muted, onEdit, onRemove }: RowProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const close = () => {
    setDraft(null)
    setError(null)
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    const next = draft?.trim()
    if (!next || next === text) return close()
    const why = await onEdit(next)
    if (why) setError(why)
    else close()
  }
  if (draft !== null) {
    return (
      <form onSubmit={save} className="-mx-3 flex flex-col gap-2 rounded-lg bg-card px-3 py-2.5">
        <Input
          autoFocus
          aria-label="Edit line"
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
      <span className={cn('flex-1', muted && 'text-muted-foreground')}>
        <Marked text={text} query={query} />
      </span>
      <span className="flex opacity-0 group-focus-within:opacity-100 group-hover:opacity-100">
        <IconButton label="Edit" onClick={() => setDraft(text)}>
          <Pencil />
        </IconButton>
        <IconButton label={removeLabel} onClick={onRemove}>
          <Trash2 />
        </IconButton>
      </span>
      {date ? <span className="shrink-0 text-muted-foreground">{date}</span> : null}
    </div>
  )
}

/** A rounded field like the Talk page's. Enter adds; pasted lines add one each; a refusal shows under
 * it and what was not added stays in the field. */
function AddField({ label, onAdd }: { label: string; onAdd: (lines: string[]) => Promise<string[] | null> }) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const add = async (lines: string[]) => {
    const left = await onAdd(lines.map((l) => l.trim()).filter(Boolean))
    if (left) {
      setError(left[0])
      setText(left.slice(1).join(' '))
    } else {
      setError(null)
      setText('')
    }
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (text.trim()) void add([text])
  }
  const paste = (event: ClipboardEvent<HTMLInputElement>) => {
    const pasted = event.clipboardData.getData('text')
    if (!pasted.includes('\n')) return
    event.preventDefault()
    void add(pasted.split(/\r?\n/).map((l) => l.replace(/^\s*[-*]\s+/, '')))
  }
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
          onPaste={paste}
          className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
        />
      </label>
      {error ? <span className="px-4 text-sm text-destructive">{error}</span> : null}
    </form>
  )
}

/** Edit all: every line of a section in one box, for pasting or rewriting many at once. */
function EditAll({
  lines,
  label,
  onSave,
  onClose,
}: {
  lines: string[]
  label: string
  onSave: (lines: string[]) => Result
  onClose: () => void
}) {
  const [text, setText] = useState(lines.join('\n'))
  const [error, setError] = useState<string | null>(null)
  const save = async (event: FormEvent) => {
    event.preventDefault()
    const why = await onSave(
      text
        .split(/\r?\n/)
        .map((l) => l.replace(/^\s*[-*]\s+/, '').trim())
        .filter(Boolean),
    )
    if (why) setError(why)
    else onClose()
  }
  return (
    <form onSubmit={save} className="-mx-3 flex flex-col gap-2 rounded-lg bg-card px-3 py-3">
      <Textarea
        autoFocus
        aria-label={label}
        aria-invalid={Boolean(error)}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setError(null)
        }}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
        className="min-h-40 bg-background text-base md:text-base"
      />
      {error ? <span className="text-sm text-destructive">{error}</span> : null}
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto text-sm text-muted-foreground">One per line</span>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit">Save</Button>
      </div>
    </form>
  )
}

type HeadingProps = {
  title: string
  onEditAll: () => void
  onRename?: (title: string) => Result
  onDelete?: () => void
  aside?: ReactNode
}

/** A section's title with its actions on hover: edit all, rename and delete. */
function Heading({ title, onEditAll, onRename, onDelete, aside }: HeadingProps) {
  const [name, setName] = useState<string | null>(null)
  const rename = async (event: FormEvent) => {
    event.preventDefault()
    const next = name?.trim()
    if (!next || next === title || !onRename) return setName(null)
    const why = await onRename(next)
    if (why) toast.error(why)
    else setName(null)
  }
  if (name !== null) {
    return (
      <form onSubmit={rename} className="mb-1.5 flex items-center gap-2">
        <Input
          autoFocus
          aria-label="Topic name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && setName(null)}
          className="h-9 w-64 text-lg font-semibold md:text-lg"
        />
        <Button type="button" variant="ghost" onClick={() => setName(null)}>
          Cancel
        </Button>
        <Button type="submit">Save</Button>
      </form>
    )
  }
  return (
    <div className="group mb-1.5 flex items-center gap-1">
      <h2 className="mr-1 text-lg font-semibold">{title}</h2>
      <span className="flex opacity-0 group-focus-within:opacity-100 group-hover:opacity-100">
        <IconButton label="Edit all" onClick={onEditAll}>
          <ListCollapse />
        </IconButton>
        {onRename ? (
          <IconButton label={`Rename ${title}`} onClick={() => setName(title)}>
            <Pencil />
          </IconButton>
        ) : null}
        {onDelete ? (
          <IconButton label={`Delete ${title}`} onClick={onDelete}>
            <Trash2 />
          </IconButton>
        ) : null}
      </span>
      {aside}
    </div>
  )
}

type Persona = { title: string; behaviour: string[]; instructions: string[] }

/** How he behaves: his manner as lines, and the technical instructions folded away. Written only
 * from here, never by a conversation. */
function Behaviour({ query }: { query: string }) {
  const persona = $api.useQuery('get', '/api/persona')
  const put = $api.useMutation('put', '/api/persona', OWN_ERRORS)
  const [editing, setEditing] = useState<'behaviour' | 'instructions' | null>(null)
  const [open, setOpen] = useState(false)
  const p = persona.data
  if (!p) return null

  const write = (next: Partial<Persona>): Result =>
    attempt(async () => {
      await put.mutateAsync({
        body: { behaviour: next.behaviour ?? p.behaviour, instructions: next.instructions ?? p.instructions },
      })
      void persona.refetch()
    })
  const save = async (next: Partial<Persona>): Result => {
    const why = await write(next)
    if (!why) toast.success(SAVED)
    return why
  }
  // Undo puts the text from before back; kept here, so it works until the page reloads.
  const undo = (key: 'behaviour' | 'instructions', before: string[]) => ({
    label: 'Undo',
    onClick: () => void save({ [key]: before }).then((why) => why && toast.error(why)),
  })
  const replace = (list: string[], at: number, text: string | null) =>
    text === null ? list.filter((_, i) => i !== at) : list.map((l, i) => (i === at ? text : l))
  const remove = (key: 'behaviour' | 'instructions', at: number) => {
    const before = p[key]
    void write({ [key]: replace(before, at, null) }).then((why) => {
      if (why) return toast.error(why)
      toast(`Removed: ${before[at]}`, { action: undo(key, before) })
    })
  }
  const saveAll = async (key: 'behaviour' | 'instructions', lines: string[]): Result => {
    const before = p[key]
    const why = await write({ [key]: lines })
    if (!why) toast(SAVED, { action: undo(key, before) })
    return why
  }

  const rules = p.behaviour.map((r, i) => ({ r, i })).filter(({ r }) => has(r, query))
  const tech = p.instructions.map((r, i) => ({ r, i })).filter(({ r }) => has(r, query))
  if (query && rules.length === 0 && tech.length === 0) return null
  return (
    <section aria-label="How he behaves" className="flex flex-col gap-0.5">
      <Heading
        title="How he behaves"
        onEditAll={() => setEditing('behaviour')}
        aside={
          <span className="ml-auto flex items-center gap-1.5 text-sm text-muted-foreground">
            <Lock className="size-3.5" />
            Only you can change these
          </span>
        }
      />
      {editing === 'behaviour' ? (
        <EditAll
          lines={p.behaviour}
          label="How he behaves"
          onSave={(l) => saveAll('behaviour', l)}
          onClose={() => setEditing(null)}
        />
      ) : (
        <>
          {rules.map(({ r, i }) => (
            <LineRow
              key={`${i}${r}`}
              text={r}
              query={query}
              removeLabel="Remove"
              onEdit={(t) => save({ behaviour: replace(p.behaviour, i, t) })}
              onRemove={() => remove('behaviour', i)}
            />
          ))}
          {query ? null : (
            <AddField
              label="Add a rule"
              onAdd={async (lines) => {
                const why = await save({ behaviour: [...p.behaviour, ...lines] })
                return why ? [why, ...lines] : null
              }}
            />
          )}
        </>
      )}
      {tech.length || !query ? (
        <div className="mt-1 flex flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              className="-ml-3 text-muted-foreground"
              aria-expanded={open || Boolean(query)}
              onClick={() => setOpen(!open)}
            >
              <ChevronRight className={cn('transition-transform', (open || query) && 'rotate-90')} />
              {`Technical instructions (${p.instructions.length})`}
            </Button>
            {open || query ? (
              <IconButton label="Edit all technical instructions" onClick={() => setEditing('instructions')}>
                <ListCollapse />
              </IconButton>
            ) : null}
          </div>
          {editing === 'instructions' ? (
            <EditAll
              lines={p.instructions}
              label="Technical instructions"
              onSave={(l) => saveAll('instructions', l)}
              onClose={() => setEditing(null)}
            />
          ) : open || query ? (
            tech.map(({ r, i }) => (
              <LineRow
                key={`${i}${r}`}
                text={r}
                query={query}
                muted
                removeLabel="Remove"
                onEdit={(t) => save({ instructions: replace(p.instructions, i, t) })}
                onRemove={() => remove('instructions', i)}
              />
            ))
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

type Actions = {
  add: (topic: string, fact: string) => Result
  edit: (topic: string, id: string, fact: string) => Result
  forget: (topic: string, fact: MemoryFact) => void
  replaceAll: (topic: string, facts: string[]) => Result
  rename: (topic: string, title: string) => Result
  remove: (topic: MemoryTopic) => void
}

function Topic({
  topic,
  query,
  dayFirst,
  act,
}: {
  topic: MemoryTopic
  query: string
  dayFirst: boolean
  act: Actions
}) {
  const [editing, setEditing] = useState(false)
  const facts = topic.facts.filter((f) => has(f.text, query) || has(topic.title, query))
  if (query && facts.length === 0) return null
  return (
    <section aria-label={topic.title} className="flex flex-col gap-0.5">
      <Heading
        title={topic.title}
        onEditAll={() => setEditing(true)}
        onRename={(t) => act.rename(topic.name, t)}
        onDelete={() => act.remove(topic)}
      />
      {editing ? (
        <EditAll
          lines={topic.facts.map((f) => f.text)}
          label={`Edit all of ${topic.title}`}
          onSave={(l) => act.replaceAll(topic.name, l)}
          onClose={() => setEditing(false)}
        />
      ) : (
        <>
          {facts.map((f) => (
            <LineRow
              key={f.id}
              text={f.text}
              date={noted(f.noted, dayFirst)}
              query={query}
              removeLabel="Forget"
              onEdit={(t) => act.edit(topic.name, f.id, t)}
              onRemove={() => act.forget(topic.name, f)}
            />
          ))}
          {query ? null : (
            <>
              {facts.length === 0 ? <span className="py-2 text-muted-foreground">Nothing yet.</span> : null}
              <AddField
                label={`Add to ${topic.title.toLowerCase()}`}
                onAdd={async (lines) => {
                  for (const [n, line] of lines.entries()) {
                    const why = await act.add(topic.name, line)
                    if (why) return [lines.length > 1 ? `"${line}": ${why}` : why, ...lines.slice(n)]
                  }
                  return null
                }}
              />
            </>
          )}
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

/** Template: Winston's page. How he behaves (his persona's manner, only the owner writes it), then what
 * he remembers: one section per topic file in data/memory, facts as stored. Search across all of it;
 * add, edit, forget, edit all, rename and delete in place. */
export default function MemoryPage() {
  const feed = useLive()
  const ask = useConfirm()
  const status = $api.useQuery('get', '/api/status')
  const memory = $api.useQuery('get', '/api/memory')
  const add = $api.useMutation('post', '/api/memory/{topic}', OWN_ERRORS)
  const edit = $api.useMutation('put', '/api/memory/{topic}', OWN_ERRORS)
  const forget = $api.useMutation('delete', '/api/memory/{topic}', OWN_ERRORS)
  const all = $api.useMutation('put', '/api/memory/{topic}/all', OWN_ERRORS)
  const addTopic = $api.useMutation('post', '/api/memory/topics', OWN_ERRORS)
  const renameTopic = $api.useMutation('put', '/api/memory/topics/{topic}', OWN_ERRORS)
  const deleteTopic = $api.useMutation('delete', '/api/memory/topics/{topic}', OWN_ERRORS)
  const restoreTopic = $api.useMutation('post', '/api/memory/topics/{topic}/restore', OWN_ERRORS)
  const [search, setSearch] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const query = search.trim().toLowerCase()

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 10_000)
    return () => clearInterval(timer)
  }, [])

  const refresh = () => void memory.refetch()
  const saved = (run: () => Promise<unknown>): Result =>
    attempt(async () => {
      await run()
      refresh()
      toast.success(memory.data?.enabled === false ? SAVED_OFF : SAVED)
    })
  const act: Actions = {
    add: (topic, fact) => saved(() => add.mutateAsync({ params: { path: { topic } }, body: { fact } })),
    edit: (topic, id, fact) => saved(() => edit.mutateAsync({ params: { path: { topic } }, body: { id, fact } })),
    replaceAll: (topic, facts) => saved(() => all.mutateAsync({ params: { path: { topic } }, body: { facts } })),
    rename: (topic, title) => saved(() => renameTopic.mutateAsync({ params: { path: { topic } }, body: { title } })),
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
                    { params: { path: { topic } }, body: { fact: fact.text, index, noted: fact.noted } },
                    { onSuccess: refresh, onError: (error) => toast.error(errorText(error)) },
                  ),
              },
            })
          },
        },
      ),
    remove: async (topic) => {
      const n = topic.facts.length
      if (n) {
        const choice = await ask({
          title: `Delete ${topic.title}?`,
          lines: [
            `Its ${n === 1 ? 'fact goes' : `${n} facts go`} with it, from his next conversation. Undo brings ${n === 1 ? 'it' : 'them'} back.`,
          ],
          choices: [{ label: 'Delete topic', value: 'yes', destructive: true }],
        })
        if (!choice) return
      }
      deleteTopic.mutate(
        { params: { path: { topic: topic.name } } },
        {
          onError: (error) => toast.error(errorText(error)),
          onSuccess: ({ text }) => {
            refresh()
            toast(`Deleted ${topic.title}`, {
              action: {
                label: 'Undo',
                onClick: () =>
                  restoreTopic.mutate(
                    { params: { path: { topic: topic.name } }, body: { text } },
                    { onSuccess: refresh, onError: (error) => toast.error(errorText(error)) },
                  ),
              },
            })
          },
        },
      )
    },
  }
  const createTopic = async (title: string) => {
    const why = await attempt(() => addTopic.mutateAsync({ body: { title } }))
    if (why) toast.error(why)
    else refresh()
    return !why
  }

  const data = memory.data
  const total = data?.topics.reduce((n, t) => n + t.facts.length, 0) ?? 0
  const shown =
    data?.topics.reduce((n, t) => n + t.facts.filter((f) => has(f.text, query) || has(t.title, query)).length, 0) ?? 0

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
        {data?.enabled === false ? (
          <span className="-mt-3 px-4 text-muted-foreground">
            He does not use these while memory is off. Turn it on in{' '}
            <Link to="/tools" className="underline underline-offset-4 hover:text-foreground">
              Tools
            </Link>
            .
          </span>
        ) : null}

        {!data && memory.isError ? (
          <LoadError error={memory.error} retrying={memory.isFetching} onRetry={refresh} />
        ) : !data ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-6 w-32" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : (
          <>
            {query ? <span className="-mt-3 text-muted-foreground">{`${shown} of ${total} facts`}</span> : null}
            <Behaviour query={query} />
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
