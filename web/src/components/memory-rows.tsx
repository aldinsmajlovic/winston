import { useState, type ClipboardEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { toast } from 'sonner'
import { errorText } from '@/api/client'
import { RowMenu } from '@/components/row-menu'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useRegion } from '@/hooks/use-region'
import { cn } from '@/lib/utils'

// The rows Memory and Personality share: documents that save each edit, with Undo in the toast.

export const SAVED = 'Saved. He knows it from his next conversation.'
/** A fold's toggle: its text lines up with the column, and an open fold does not look pressed. */
export const FOLD = '-ml-3 text-muted-foreground aria-expanded:bg-transparent aria-expanded:text-muted-foreground'

/** What a save returns: why it was refused, or null. */
export type Result = Promise<string | null>

/** A toast saying what was done, with Undo running `restore`. */
// eslint-disable-next-line react-refresh/only-export-components -- shared with the rows' pages
export function undoToast(text: string, restore: () => void) {
  toast(text, { action: { label: 'Undo', onClick: restore } })
}

// eslint-disable-next-line react-refresh/only-export-components -- shared with the rows' pages
export async function attempt(run: () => Promise<unknown>): Result {
  try {
    await run()
    return null
  } catch (error) {
    return errorText(error)
  }
}

/** Enter saves a growing field; its text is one paragraph, so there is no line to break. */
function enterSubmits(event: KeyboardEvent<HTMLTextAreaElement>) {
  if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
  event.preventDefault()
  event.currentTarget.form?.requestSubmit()
}

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
  /** When a fact was noted (YYYY-MM-DD), shown muted as "noted 23 Sep". */
  noted?: string | null
  query?: string
  /** The menu's word for removing it: Forget, Delete. */
  removeLabel: string
  muted?: boolean
  /** A paragraph (a rule) edits in a growing field; a fact in one line. */
  multiline?: boolean
  /** A fact's last day (YYYY-MM-DD): a muted "Until" badge, and a date field when editing. Facts pass
   * it even when unset (null), rules leave it out. */
  until?: string | null
  /** until is undefined when the date field was left as it was. */
  onEdit: (text: string, until?: string | null) => Result
  onRemove: () => void
}

/** One fact or rule: plain text with its menu (edit, remove), edited in place. */
export function LineRow({ text, noted, query = '', removeLabel, muted, multiline, until, onEdit, onRemove }: RowProps) {
  const region = useRegion()
  /** "2026-09-24" as the Region settings write a date. */
  const day = (date: string) => region.date(new Date(`${date}T12:00:00`))
  const [draft, setDraft] = useState<string | null>(null)
  const [date, setDate] = useState('')
  const [error, setError] = useState<string | null>(null)
  const dated = until !== undefined
  const close = () => {
    setDraft(null)
    setError(null)
  }
  const open = () => {
    setDraft(text)
    setDate(until ?? '')
  }
  const save = async (event: FormEvent) => {
    event.preventDefault()
    const next = draft?.trim()
    const moved = dated && date !== (until ?? '')
    if (!next || (next === text && !moved)) return close()
    const why = await onEdit(next, moved ? date || null : undefined)
    if (why) setError(why)
    else close()
  }
  if (draft !== null) {
    const field = {
      autoFocus: true,
      'aria-label': 'Edit line',
      'aria-invalid': Boolean(error),
      value: draft,
      onChange: (e: { target: { value: string } }) => {
        setDraft(e.target.value)
        setError(null)
      },
    }
    return (
      <form onSubmit={save} className="-mx-3 flex flex-col gap-2 rounded-lg bg-card px-3 py-2.5">
        {multiline ? (
          <Textarea
            {...field}
            onKeyDown={(e) => (e.key === 'Escape' ? close() : enterSubmits(e))}
            className="min-h-0 bg-background text-base md:text-base"
          />
        ) : (
          <Input {...field} onKeyDown={(e) => e.key === 'Escape' && close()} />
        )}
        {error ? <span className="text-sm text-destructive">{error}</span> : null}
        <div className="flex flex-wrap items-center justify-end gap-2">
          {dated ? (
            <label className="mr-auto flex items-center gap-2 text-sm text-muted-foreground">
              Until
              <Input
                type="date"
                aria-label="Until"
                value={date}
                onChange={(e) => {
                  setDate(e.target.value)
                  setError(null)
                }}
                className="h-8 w-40"
              />
            </label>
          ) : null}
          <Button type="button" variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit">Save</Button>
        </div>
      </form>
    )
  }
  return (
    <div className="-mx-3 flex items-start gap-3 rounded-lg px-3 py-2 hover:bg-card">
      {/* Below a phone's width the dates wrap under the text. */}
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
        <span className={cn('min-w-0 flex-1 basis-60 wrap-break-word', muted && 'text-muted-foreground')}>
          <Marked text={text} query={query} />
        </span>
        {until ? (
          <Badge variant="outline" className="font-normal text-muted-foreground">
            {`Until ${day(until)}`}
          </Badge>
        ) : null}
        {noted ? <span className="shrink-0 text-muted-foreground">{`noted ${day(noted)}`}</span> : null}
      </div>
      <span className="-my-0.5 flex">
        <RowMenu
          name={text}
          items={[
            { label: 'Edit', onSelect: open },
            { label: removeLabel, onSelect: onRemove, destructive: true },
          ]}
        />
      </span>
    </div>
  )
}

type AddProps = {
  label: string
  placeholder?: string
  /** A growing field, for paragraphs (rules). */
  multiline?: boolean
  onAdd: (lines: string[]) => Promise<string[] | null>
}

/** A rounded field like the Talk page's. Enter adds; pasted lines add one each; a refusal shows under
 * it and what was not added stays in the field. */
export function AddField({ label, placeholder = label, multiline, onAdd }: AddProps) {
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
  const paste = (event: ClipboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const pasted = event.clipboardData.getData('text')
    if (!pasted.includes('\n')) return
    event.preventDefault()
    void add(pasted.split(/\r?\n/).map((l) => l.replace(/^\s*[-*]\s+/, '')))
  }
  const field = {
    'aria-label': label,
    'aria-invalid': Boolean(error),
    placeholder,
    value: text,
    onChange: (e: { target: { value: string } }) => {
      setText(e.target.value)
      setError(null)
    },
    onPaste: paste,
    className: 'w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground',
  }
  return (
    <form onSubmit={submit} className="mt-1 flex flex-col gap-1">
      <label
        className={cn(
          'flex gap-2.5 rounded-3xl bg-card px-4 text-muted-foreground focus-within:ring-3 focus-within:ring-ring/50',
          multiline ? 'min-h-10 items-start py-2' : 'h-10 items-center',
          error && 'ring-1 ring-destructive',
        )}
      >
        <Plus className={cn('size-4 shrink-0', multiline && 'mt-1')} />
        {multiline ? (
          <textarea {...field} rows={1} onKeyDown={enterSubmits} className={cn(field.className, 'field-sizing-content resize-none')} />
        ) : (
          <input {...field} />
        )}
      </label>
      {error ? <span className="px-4 text-sm text-destructive">{error}</span> : null}
    </form>
  )
}

/** Edit all: every line of a section in one box, for pasting or rewriting many at once. */
export function EditAll({
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

type TopicFormProps = {
  title?: string
  description?: string
  submit: string
  onSave: (title: string, description: string) => Result
  onClose: () => void
}

/** A topic's name and what belongs in it, asked together: new, or renamed and described. */
export function TopicForm({ title = '', description = '', submit, onSave, onClose }: TopicFormProps) {
  const [name, setName] = useState(title)
  const [about, setAbout] = useState(description)
  const [error, setError] = useState<string | null>(null)
  const save = async (event: FormEvent) => {
    event.preventDefault()
    const next = name.trim()
    if (!next || (next === title && about.trim() === description)) return onClose()
    const why = await onSave(next, about.trim())
    if (why) setError(why)
    else onClose()
  }
  const escape = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
  return (
    <form onSubmit={save} className="-mx-3 mb-1.5 flex flex-col gap-3 rounded-lg bg-card px-3 py-3">
      <label className="flex flex-col gap-2 text-sm font-medium">
        Topic name
        <Input autoFocus value={name} aria-invalid={Boolean(error)} onChange={(e) => setName(e.target.value)} onKeyDown={escape} />
      </label>
      <label className="flex flex-col gap-2 text-sm font-medium">
        What belongs here
        <Input value={about} onChange={(e) => setAbout(e.target.value)} onKeyDown={escape} />
        <span className="font-normal text-muted-foreground">He reads this to choose where a new fact goes.</span>
      </label>
      {error ? <span className="text-sm text-destructive">{error}</span> : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit">{submit}</Button>
      </div>
    </form>
  )
}

type HeadingProps = {
  title: string
  /** What belongs in a topic: a muted line under it, edited with the name. */
  description?: string
  onEditAll: () => void
  onRename?: (title: string, description: string) => Result
  onDelete?: () => void
}

/** A section's title, its description under it, and its menu: edit all as text, rename and delete. */
export function Heading({ title, description, onEditAll, onRename, onDelete }: HeadingProps) {
  const [renaming, setRenaming] = useState(false)
  if (renaming && onRename) {
    return (
      <TopicForm title={title} description={description} submit="Save" onSave={onRename} onClose={() => setRenaming(false)} />
    )
  }
  return (
    <div className="mb-1.5 flex flex-col gap-0.5">
      <div className="flex items-center gap-3">
        <h3 className="min-w-0 flex-1 text-lg font-semibold">{title}</h3>
        <RowMenu
          name={title}
          items={[
            { label: 'Edit all as text', onSelect: onEditAll },
            ...(onRename ? [{ label: 'Rename and describe', onSelect: () => setRenaming(true) }] : []),
            ...(onDelete ? [{ label: 'Delete topic', onSelect: onDelete, destructive: true }] : []),
          ]}
        />
      </div>
      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
    </div>
  )
}
