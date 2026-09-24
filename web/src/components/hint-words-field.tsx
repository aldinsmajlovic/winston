import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import type { FieldProps } from '@rjsf/utils'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { KindIcon } from '@/components/kind-icon'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useOptions } from '@/hooks/use-options'

/** Words typed or pasted at once: one per line or comma. */
function split(text: string): string[] {
  return text
    .split(/[\n,;]+/)
    .map((w) => w.trim())
    .filter(Boolean)
}

/** stt.hint_words: the words Winston adds by himself as badges with their source icon (locked, the source
 * in the tooltip), the user's own after them with a Remove x, and a box to add more. The (i) adds what the
 * current engine does with them, from the server. */
export function HintWordsField({ schema, formData, onChange, fieldPathId, disabled, readonly }: FieldProps) {
  const { choices, source } = useOptions('hint_words')
  const [text, setText] = useState('')
  const mine = Array.isArray(formData) ? (formData as string[]) : []
  const locked = disabled || readonly
  const known = new Set([...choices.map((c) => c.value.toLowerCase()), ...mine.map((w) => w.toLowerCase())])
  const typed = split(text)
  const clash = typed.find((w) => known.has(w.toLowerCase()))
  const spaced = typed.find((w) => /\s/.test(w))
  const set = (next: string[]) => onChange(next, fieldPathId.path)
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
        <InfoTip text={[schema.description, source].filter(Boolean).join(' ')} />
      </div>
      {choices.length || mine.length ? (
        <div className="flex flex-wrap gap-1">
          {choices.map((c) => (
            <Tooltip key={`auto-${c.value}`}>
              <TooltipTrigger asChild>
                <Badge variant="secondary" tabIndex={0}>
                  <KindIcon name={c.icon} />
                  {c.label}
                </Badge>
              </TooltipTrigger>
              <TooltipContent>{c.hint}</TooltipContent>
            </Tooltip>
          ))}
          {mine.map((w, i) => (
            <Badge key={`mine-${w}`} variant="secondary" className="pr-0.5">
              {w}
              <IconButton
                label={`Remove ${w}`}
                type="button"
                size="icon-xs"
                className="size-4 rounded-full"
                disabled={locked}
                onClick={() => set(mine.filter((_, j) => j !== i))}
              >
                <X />
              </IconButton>
            </Badge>
          ))}
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <Input
          id={`${id}-add`}
          value={text}
          placeholder="Add a word"
          disabled={locked}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            // A pasted list adds each word it does not already hold.
            const words = split(e.clipboardData.getData('text'))
            if (words.length < 2) return
            e.preventDefault()
            const seen = new Set(known)
            const fresh = words.filter((w) => !/\s/.test(w) && !seen.has(w.toLowerCase()) && seen.add(w.toLowerCase()))
            if (fresh.length) set([...mine, ...fresh])
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
      {clash ? <span className="text-sm text-muted-foreground">Already in the list</span> : null}
      {spaced && !clash ? <span className="text-sm text-muted-foreground">One word each</span> : null}
    </div>
  )
}
