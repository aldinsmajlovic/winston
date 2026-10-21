import { useState } from 'react'
import { Link } from 'react-router'
import { ChevronRight, Plus, Search, X } from 'lucide-react'
import { toast } from 'sonner'
import { $api, errorText, type MemoryFact, type MemoryTopic } from '@/api/client'
import { presence, useLive } from '@/api/live'
import { InfoTip } from '@/components/info-tip'
import {
  AddField,
  EditAll,
  FOLD,
  Heading,
  LineRow,
  SAVED,
  TopicForm,
  attempt,
  undoToast,
  type Result,
} from '@/components/memory-rows'
import { LoadError, useTitle } from '@/components/page'
import { WinstonHeader } from '@/components/winston-header'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { useConfirm } from '@/hooks/use-confirm'
import { useNow } from '@/hooks/use-now'
import { cn } from '@/lib/utils'

const SAVED_OFF = 'Saved. He does not use it while memory is off.'
// Actions that show their own errors, in place or as a toast.
const OWN_ERRORS = { meta: { showsErrors: true } }

const has = (text: string, query: string) => !query || text.toLowerCase().includes(query)

type Actions = {
  add: (topic: string, fact: string) => Result
  edit: (topic: string, fact: MemoryFact, text: string, until?: string | null) => Result
  forget: (topic: string, fact: MemoryFact) => void
  replaceAll: (topic: string, facts: string[]) => Result
  rename: (topic: string, title: string, description: string) => Result
  remove: (topic: MemoryTopic) => void
}

function Topic({
  topic,
  query,
  act,
}: {
  topic: MemoryTopic
  query: string
  act: Actions
}) {
  const [editing, setEditing] = useState(false)
  const facts = topic.facts.filter((f) => !f.expired && (has(f.text, query) || has(topic.title, query)))
  if (query && facts.length === 0) return null
  return (
    <section aria-label={topic.title} className="flex flex-col gap-0.5">
      <Heading
        title={topic.title}
        description={topic.description}
        onEditAll={() => setEditing(true)}
        onRename={(t, d) => act.rename(topic.name, t, d)}
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
              noted={f.noted}
              until={f.until ?? null}
              query={query}
              removeLabel="Forget"
              onEdit={(t, u) => act.edit(topic.name, f, t, u)}
              onRemove={() => act.forget(topic.name, f)}
            />
          ))}
          {query ? null : (
            <AddField
              label={`Add to ${topic.title}`}
              onAdd={async (lines) => {
                for (const [n, line] of lines.entries()) {
                  const why = await act.add(topic.name, line)
                  if (why) return [lines.length > 1 ? `"${line}": ${why}` : why, ...lines.slice(n)]
                }
                return null
              }}
            />
          )}
        </>
      )}
    </section>
  )
}

/** Facts whose day has passed, from every topic: folded and greyed at the bottom, out of his prompt
 * but kept until cleared. Editing one's date brings it back. */
function Expired({
  topics,
  query,
  act,
  onClear,
}: {
  topics: MemoryTopic[]
  query: string
  act: Actions
  onClear: (count: number) => void
}) {
  const [open, setOpen] = useState(false)
  const rows = topics.flatMap((t) =>
    t.facts.filter((f) => f.expired && (has(f.text, query) || has(t.title, query))).map((f) => ({ t, f })),
  )
  if (rows.length === 0) return null
  const shown = open || Boolean(query)
  return (
    <section aria-label="Expired" className="flex flex-col gap-0.5">
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          className={FOLD}
          aria-expanded={shown}
          onClick={() => setOpen(!open)}
        >
          <ChevronRight className={cn('transition-transform', shown && 'rotate-90')} />
          {`Expired (${rows.length})`}
        </Button>
        <InfoTip text="Their day has passed, so he no longer has them in mind. Give one a later date to bring it back." />
        {query ? null : (
          <Button type="button" variant="ghost" className="-mr-3 ml-auto text-muted-foreground" onClick={() => onClear(rows.length)}>
            Clear expired
          </Button>
        )}
      </div>
      {shown
        ? rows.map(({ t, f }) => (
            <LineRow
              key={f.id}
              text={f.text}
              noted={f.noted}
              until={f.until ?? null}
              query={query}
              muted
              removeLabel="Forget"
              onEdit={(text, until) => act.edit(t.name, f, text, until)}
              onRemove={() => act.forget(t.name, f)}
            />
          ))
        : null}
    </section>
  )
}

/** Template: Winston's page. What he remembers: one section per topic file in data/memory, facts as
 * stored, with the topic's description under its heading. Facts past their until fold under Expired at
 * the bottom. Search across all of it; add, edit, forget, edit all, rename and delete in place, from
 * each line's and heading's menu. His personality is on Assistant, Personality. */
export default function MemoryPage() {
  useTitle('Memory')
  const feed = useLive()
  const ask = useConfirm()
  const status = $api.useQuery('get', '/api/status')
  const memory = $api.useQuery('get', '/api/memory')
  const add = $api.useMutation('post', '/api/memory/{topic}', OWN_ERRORS)
  const edit = $api.useMutation('put', '/api/memory/{topic}', OWN_ERRORS)
  const forget = $api.useMutation('delete', '/api/memory/{topic}')
  const all = $api.useMutation('put', '/api/memory/{topic}/all', OWN_ERRORS)
  const addTopic = $api.useMutation('post', '/api/memory/topics', OWN_ERRORS)
  const renameTopic = $api.useMutation('put', '/api/memory/topics/{topic}', OWN_ERRORS)
  const deleteTopic = $api.useMutation('delete', '/api/memory/topics/{topic}')
  const restoreTopic = $api.useMutation('post', '/api/memory/topics/{topic}/restore')
  const clearExpired = $api.useMutation('post', '/api/memory/expired/clear')
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const now = useNow(10_000)
  const query = search.trim().toLowerCase()

  const refresh = () => void memory.refetch()
  const saved = (run: () => Promise<unknown>): Result =>
    attempt(async () => {
      await run()
      refresh()
      toast.success(memory.data?.enabled === false ? SAVED_OFF : SAVED)
    })
  const act: Actions = {
    add: (topic, fact) => saved(() => add.mutateAsync({ params: { path: { topic } }, body: { fact } })),
    // Undo puts the old text and until back on the line the edit stored.
    edit: (topic, fact, text, until) =>
      attempt(async () => {
        const now = await edit.mutateAsync({
          params: { path: { topic } },
          body: until === undefined ? { id: fact.id, fact: text } : { id: fact.id, fact: text, until },
        })
        refresh()
        undoToast(memory.data?.enabled === false ? SAVED_OFF : SAVED, () =>
          edit.mutate(
            { params: { path: { topic } }, body: { id: now.id, fact: fact.text, until: fact.until ?? null } },
            { onSuccess: refresh, onError: (error) => toast.error(errorText(error)) },
          ),
        )
      }),
    replaceAll: (topic, facts) => saved(() => all.mutateAsync({ params: { path: { topic } }, body: { facts } })),
    rename: (topic, title, description) =>
      saved(() => renameTopic.mutateAsync({ params: { path: { topic } }, body: { title, description } })),
    forget: (topic, fact) =>
      forget.mutate(
        { params: { path: { topic }, query: { id: fact.id } } },
        {
          onSuccess: ({ index }) => {
            refresh()
            undoToast(`Forgot: ${fact.text}`, () =>
              add.mutate(
                {
                  params: { path: { topic } },
                  body: { fact: fact.text, index, noted: fact.noted, until: fact.until },
                },
                { onSuccess: refresh, onError: (error) => toast.error(errorText(error)) },
              ),
            )
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
          onSuccess: ({ text }) => {
            refresh()
            undoToast(`Deleted ${topic.title}`, () =>
              restoreTopic.mutate({ params: { path: { topic: topic.name } }, body: { text } }, { onSuccess: refresh }),
            )
          },
        },
      )
    },
  }
  const clear = async (count: number) => {
    const choice = await ask({
      title: count === 1 ? 'Clear the expired fact?' : `Clear ${count} expired facts?`,
      lines: [`${count === 1 ? 'It leaves' : 'They leave'} his memory files too. Undo brings ${count === 1 ? 'it' : 'them'} back.`],
      choices: [{ label: 'Clear expired', value: 'yes', destructive: true }],
    })
    if (!choice) return
    clearExpired.mutate(
      {},
      {
        onSuccess: (gone) => {
          refresh()
          undoToast(gone.length === 1 ? 'Cleared 1 expired fact' : `Cleared ${gone.length} expired facts`, () =>
            void (async () => {
              // One at a time, in order, so each goes back to its place.
              for (const g of gone) {
                await add.mutateAsync({
                  params: { path: { topic: g.topic } },
                  body: { fact: g.text, index: g.index, noted: g.noted, until: g.until },
                })
              }
            })()
              .catch((error) => toast.error(errorText(error)))
              .finally(refresh),
          )
        },
      },
    )
  }
  const createTopic = (title: string, description: string) =>
    attempt(async () => {
      await addTopic.mutateAsync({ body: { title, description: description || null } })
      refresh()
    })

  const data = memory.data
  const total = data?.topics.reduce((n, t) => n + t.facts.length, 0) ?? 0
  const shown =
    data?.topics.reduce((n, t) => n + t.facts.filter((f) => has(f.text, query) || has(t.title, query)).length, 0) ?? 0
  const link = 'underline underline-offset-4 hover:text-foreground'

  return (
    <div className="flex min-h-svh flex-col items-center">
      <WinstonHeader name={status.data?.assistant ?? ''} state={presence(feed, now)} />
      <main className="flex w-full max-w-3xl flex-1 flex-col gap-7 px-4 pb-8">
        <div className="flex flex-col gap-1.5">
          <h2 className="text-xl font-semibold">Memory</h2>
          <p className="text-muted-foreground">
            What he remembers about you: he reads it at the start of every conversation and adds to it when you ask
            him to remember something.
          </p>
          {data?.enabled === false ? (
            <p className="text-muted-foreground">
              He does not use these while memory is off. Turn it on in{' '}
              <Link to="/assistant" className={link}>
                Assistant
              </Link>
              .
            </p>
          ) : null}
          <p className="text-muted-foreground">
            His manner and rules are in{' '}
            <Link to="/assistant/personality" className={link}>
              Settings, Assistant, Personality
            </Link>
            .
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex h-11 min-w-0 flex-1 items-center gap-2.5 rounded-full bg-card px-4 text-muted-foreground focus-within:ring-3 focus-within:ring-ring/50">
            <Search className="size-4 shrink-0" />
            <input
              role="searchbox"
              aria-label="Search memory"
              placeholder="Search memory"
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
          <Button type="button" variant="ghost" className="h-11 text-muted-foreground" onClick={() => setCreating(true)}>
            <Plus />
            New topic
          </Button>
        </div>
        {creating ? <TopicForm submit="Add topic" onSave={createTopic} onClose={() => setCreating(false)} /> : null}

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
            {query ? <span className="-mt-3 text-muted-foreground">{shown === 0 ? 'Nothing matches.' : `${shown} of ${total} facts`}</span> : null}
            {data.topics.map((t) => (
              <Topic key={t.name} topic={t} query={query} act={act} />
            ))}
            <Expired topics={data.topics} query={query} act={act} onClear={clear} />
            {data.chars > 0 && !query ? (
              <div className="mt-auto flex items-center gap-3 pt-4 text-sm text-muted-foreground">
                <Progress
                  aria-label="Memory in every conversation"
                  value={Math.min(100, (data.chars / data.limit) * 100)}
                  className="w-30 shrink-0"
                />
                <span>{`Read in every conversation: ${data.chars.toLocaleString()} of about ${data.limit.toLocaleString()} characters. Past that, his replies get slower.`}</span>
              </div>
            ) : null}
          </>
        )}
      </main>
    </div>
  )
}
