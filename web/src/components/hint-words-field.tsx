import { useState, type ReactNode } from 'react'
import { ChevronRight, Plus, Undo2, X } from 'lucide-react'
import type { FieldProps } from '@rjsf/utils'
import { Link } from 'react-router'
import { useConfig } from '@/api/config'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { KindIcon } from '@/components/kind-icon'
import type { FormContext } from '@/components/schema-form'
import { ExtensionIcon } from '@/components/tool-line'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useOptions } from '@/hooks/use-options'
import { getIn } from '@/lib/schema'
import { cn } from '@/lib/utils'

const REMOVED = ['stt', 'hint_words_removed']
const LINK = 'underline underline-offset-4 hover:text-foreground'

/** A fold's toggle: its text lines up with the column, and an open fold does not look pressed. */
const FOLD = '-ml-3 self-start text-muted-foreground aria-expanded:bg-transparent aria-expanded:text-muted-foreground'

// Where his own words come from, by the list's group; an extension's group is `extension:<id>`, named by
// its hint and drawn with its own icon.
const SOURCES: Record<string, { title: string; icon: string }> = {
  assistant: { title: 'His name and voices', icon: 'assistant' },
  voice: { title: 'His name and voices', icon: 'assistant' },
  home_assistant: { title: 'Home Assistant', icon: 'homeassistant' },
  contact: { title: 'Discord contacts', icon: 'discord' },
}

type Choice = ReturnType<typeof useOptions>['choices'][number]
type Source = { title: string; icon: ReactNode; words: Choice[] }

/** His words by where they come from, in the order the list gives them. */
function bySource(choices: Choice[]): Source[] {
  const out = new Map<string, Source>()
  for (const c of choices) {
    const known = SOURCES[c.group ?? '']
    const title = known?.title ?? c.hint ?? c.group ?? ''
    const source: Source = out.get(title) ?? { title, icon: known ? <KindIcon name={known.icon} /> : <ExtensionIcon name={c.icon} />, words: [] }
    source.words.push(c)
    out.set(title, source)
  }
  return [...out.values()]
}

/** Words typed or pasted at once: one per line or comma. */
function split(text: string): string[] {
  return text
    .split(/[\n,;]+/)
    .map((w) => w.trim())
    .filter(Boolean)
}

/** A word as a chip with its remove (or add back) button. */
function Chip({ word, gone, label, disabled, onClick }: { word: string; gone?: boolean; label: string; disabled?: boolean; onClick: () => void }) {
  return (
    <Badge variant="secondary" className="pr-0.5">
      <span className={cn(gone && 'text-muted-foreground line-through')}>{word}</span>
      <IconButton label={label} type="button" size="icon-xs" className="size-4 rounded-full" disabled={disabled} onClick={onClick}>
        {gone ? <Undo2 /> : <X />}
      </IconButton>
    </Badge>
  )
}

/** One source's words behind a fold, closed at first: "Home Assistant (6)". */
function SourceFold({ source, chip }: { source: Source; chip: (c: Choice) => ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex flex-col gap-1">
      <Button type="button" variant="ghost" className={FOLD} aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight className={cn('transition-transform', open && 'rotate-90')} />
        {source.icon}
        {`${source.title} (${source.words.length})`}
      </Button>
      {open ? <div className="flex flex-wrap gap-1">{source.words.map(chip)}</div> : null}
    </div>
  )
}

/** stt.hint_words: your own words first, as chips with a box to add more; then the words he adds by
 * himself, folded by where they come from. Removing one of his crosses it out (stt.hint_words_removed)
 * with an Add back. Under them, what hearing does with the words, from the server. */
export function HintWordsField({ schema, formData, onChange, fieldPathId, disabled, readonly, registry }: FieldProps) {
  const { choices, source } = useOptions('hint_words')
  const recording = useConfig().data?.logging?.turns?.enabled === true
  const [text, setText] = useState('')
  const [skipped, setSkipped] = useState(0)
  const context = registry.formContext as FormContext | undefined
  const mine = Array.isArray(formData) ? (formData as string[]) : []
  const removed = (getIn(context?.config, REMOVED) as string[] | undefined) ?? []
  const off = new Set(removed.map((w) => w.toLowerCase()))
  const locked = disabled || readonly
  const known = new Set([...choices.map((c) => c.value.toLowerCase()), ...mine.map((w) => w.toLowerCase())])
  const typed = split(text)
  const clash = typed.find((w) => known.has(w.toLowerCase()))
  const spaced = typed.find((w) => /\s/.test(w))
  // One of his words you removed, typed again: offered back rather than refused.
  const back = clash && off.has(clash.toLowerCase()) ? choices.find((c) => c.value.toLowerCase() === clash.toLowerCase()) : undefined
  const set = (next: string[]) => onChange(next, fieldPathId.path)
  const setRemoved = (word: string, gone: boolean) =>
    context?.setAt?.(REMOVED, gone ? [...removed, word] : removed.filter((w) => w.toLowerCase() !== word.toLowerCase()))
  const add = () => {
    if (!typed.length || clash || spaced) return
    set([...mine, ...typed])
    setText('')
  }
  const id = fieldPathId.$id
  return (
    <div id={id} className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <label htmlFor={`${id}-add`} className="text-sm leading-none font-medium">
          {schema.title}
        </label>
        <InfoTip text={schema.description} label={schema.title} />
      </div>
      {typeof schema.help === 'string' ? <p className="text-sm text-muted-foreground">{schema.help}</p> : null}
      {mine.length ? (
        <div className="flex flex-wrap gap-1">
          {mine.map((w, i) => (
            <Chip key={`mine-${w}`} word={w} label={`Remove ${w}`} disabled={locked} onClick={() => set(mine.filter((_, j) => j !== i))} />
          ))}
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <Input
          id={`${id}-add`}
          value={text}
          placeholder="Add a word"
          disabled={locked}
          onChange={(e) => {
            setText(e.target.value)
            setSkipped(0)
          }}
          onPaste={(e) => {
            // A pasted list adds each word it does not already hold, and says how many had spaces.
            const words = split(e.clipboardData.getData('text'))
            if (words.length < 2) return
            e.preventDefault()
            const seen = new Set(known)
            const fresh = words.filter((w) => !/\s/.test(w) && !seen.has(w.toLowerCase()) && seen.add(w.toLowerCase()))
            if (fresh.length) set([...mine, ...fresh])
            setSkipped(words.filter((w) => /\s/.test(w)).length)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <IconButton label="Add" type="button" variant="outline" disabled={locked || !typed.length || !!clash || !!spaced} onClick={add}>
          <Plus />
        </IconButton>
      </div>
      {back ? (
        <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {`You removed ${back.label} earlier.`}
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={locked}
            onClick={() => {
              setRemoved(back.value, false)
              setText('')
            }}
          >
            Add back
          </Button>
        </span>
      ) : clash ? (
        <span className="text-sm text-muted-foreground">Already in the list</span>
      ) : null}
      {spaced && !clash ? <span className="text-sm text-muted-foreground">One word each</span> : null}
      {skipped ? <span className="text-sm text-muted-foreground">{`Skipped ${skipped} with spaces: one word each`}</span> : null}
      {bySource(choices).map((s) => (
        <SourceFold
          key={s.title}
          source={s}
          chip={(c) => {
            const gone = off.has(c.value.toLowerCase())
            return (
              <Chip
                key={c.value}
                word={c.label}
                gone={gone}
                label={`${gone ? 'Add back' : 'Remove'} ${c.label}`}
                disabled={locked}
                onClick={() => setRemoved(c.value, !gone)}
              />
            )
          }}
        />
      ))}
      <p className="text-sm text-muted-foreground">
        {source ? `${source} ` : null}
        {recording ? (
          <>
            See what he misheard in{' '}
            <Link to="/history?view=marked" className={LINK}>
              History, Marked
            </Link>
            .
          </>
        ) : (
          <>
            Turn on recordings in{' '}
            <Link to="/assistant" className={LINK}>
              Assistant
            </Link>{' '}
            to see what he misheard.
          </>
        )}
      </p>
    </div>
  )
}
