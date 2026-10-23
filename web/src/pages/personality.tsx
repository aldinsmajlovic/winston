import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { toast } from 'sonner'
import { $api } from '@/api/client'
import { AddField, EditAll, FOLD, Heading, LineRow, SAVED, attempt, undoToast, type Result } from '@/components/memory-rows'
import { LoadError, Page } from '@/components/page'
import { RowMenu } from '@/components/row-menu'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useConfirm } from '@/hooks/use-confirm'
import { cn } from '@/lib/utils'

// Saves that show their own errors, in place or as a toast.
const OWN_ERRORS = { meta: { showsErrors: true } }

type Key = 'behaviour' | 'instructions'
type Persona = Record<Key, string[]>

/** His personality: how he talks and behaves, as instructions to him, and the technical instructions
 * folded away. A document: each edit saves itself with Undo, and only this page (and the setup wizard's
 * Who he is, which draws the same document) writes it, never a conversation. */
export default function PersonalityPage() {
  return (
    <Page
      title="Personality"
      back={{ to: '/assistant', label: 'Assistant' }}
      intro="How he talks and behaves, as instructions only you can change; changes apply from his next conversation."
    >
      <PersonalityDocument />
    </Page>
  )
}

/** The document itself: How he behaves, each rule editable with Undo and Add a rule, then the technical
 * instructions, folded. */
export function PersonalityDocument() {
  const persona = $api.useQuery('get', '/api/persona')
  const put = $api.useMutation('put', '/api/persona', OWN_ERRORS)
  const ask = useConfirm()
  const [editing, setEditing] = useState<Key | null>(null)
  const [open, setOpen] = useState(false)
  const p = persona.data

  const write = (next: Partial<Persona>): Result =>
    attempt(async () => {
      await put.mutateAsync({
        body: { behaviour: next.behaviour ?? p?.behaviour ?? [], instructions: next.instructions ?? p?.instructions ?? [] },
      })
      void persona.refetch()
    })
  const save = async (next: Partial<Persona>): Result => {
    const why = await write(next)
    if (!why) toast.success(SAVED)
    return why
  }
  // Undo puts the text from before back; kept here, so it works until the page reloads.
  const restore = (key: Key, before: string[]) => () => void save({ [key]: before }).then((why) => why && toast.error(why))
  const replace = (list: string[], at: number, text: string | null) =>
    text === null ? list.filter((_, i) => i !== at) : list.map((l, i) => (i === at ? text : l))
  const remove = async (key: Key, at: number) => {
    if (!p) return
    if (key === 'instructions') {
      const choice = await ask({
        title: 'Delete this instruction?',
        lines: ['An action or his speech may stop working. Undo brings it back.'],
        choices: [{ label: 'Delete', value: 'yes', destructive: true }],
      })
      if (!choice) return
    }
    const before = p[key]
    const why = await write({ [key]: replace(before, at, null) })
    if (why) toast.error(why)
    else undoToast(`Deleted: ${before[at]}`, restore(key, before))
  }
  const saveAll = async (key: Key, lines: string[]): Result => {
    const before = p?.[key] ?? []
    const why = await write({ [key]: lines })
    if (!why) undoToast(SAVED, restore(key, before))
    return why
  }
  const rows = (key: Key) =>
    p?.[key].map((r, i) => (
      <LineRow
        key={`${i}${r}`}
        text={r}
        multiline
        muted={key === 'instructions'}
        removeLabel="Delete"
        onEdit={(t) => saveAll(key, replace(p[key], i, t))}
        onRemove={() => void remove(key, i)}
      />
    ))

  return (
    <>
      {!p && persona.isError ? (
        <LoadError error={persona.error} retrying={persona.isFetching} onRetry={() => void persona.refetch()} />
      ) : !p ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-2/3" />
        </div>
      ) : (
        <>
          <section aria-label="How he behaves" className="flex flex-col gap-0.5">
            <Heading title="How he behaves" onEditAll={() => setEditing('behaviour')} />
            {editing === 'behaviour' ? (
              <EditAll
                lines={p.behaviour}
                label="How he behaves"
                onSave={(l) => saveAll('behaviour', l)}
                onClose={() => setEditing(null)}
              />
            ) : (
              <>
                {rows('behaviour')}
                <AddField
                  label="Add a rule"
                  placeholder="Write it as an instruction, like: Keep spoken replies short."
                  multiline
                  onAdd={async (lines) => {
                    const why = await save({ behaviour: [...p.behaviour, ...lines] })
                    return why ? [why, ...lines] : null
                  }}
                />
              </>
            )}
          </section>
          <section aria-label="Technical instructions" className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1">
              <Button type="button" variant="ghost" className={FOLD} aria-expanded={open} onClick={() => setOpen(!open)}>
                <ChevronRight className={cn('transition-transform', open && 'rotate-90')} />
                {`Technical instructions (${p.instructions.length})`}
              </Button>
              <span className="flex-1" />
              <RowMenu
                name="Technical instructions"
                items={[{ label: 'Edit all as text', onSelect: () => setEditing('instructions') }]}
              />
            </div>
            <p className="mb-1.5 text-sm text-muted-foreground">
              These keep his actions and speech working. Change them only if you know why.
            </p>
            {editing === 'instructions' ? (
              <EditAll
                lines={p.instructions}
                label="Technical instructions"
                onSave={(l) => saveAll('instructions', l)}
                onClose={() => setEditing(null)}
              />
            ) : open ? (
              rows('instructions')
            ) : null}
          </section>
        </>
      )}
    </>
  )
}
