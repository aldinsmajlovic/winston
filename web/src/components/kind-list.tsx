import { BellOff } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

/** One kind of message an extension may bring up, as the list draws it; pages map KindView into this. */
export type KindItem = {
  kind: string
  title: string
  /** What he will say and when; may be ''. */
  description: string
  /** You asked for it (a reminder, a link): "now" skips quiet hours and the daily limit. */
  requested: boolean
  /** In effect: one value, or one per urgency level. */
  deliver: unknown
  where: unknown
  /** What the extension chose. */
  declaredDeliver: unknown
  declaredWhere: unknown
  enabled: boolean
  /** "Muted until Mon 08:00" while it is muted. */
  mutedText?: string
}

type Change = { deliver?: unknown; where?: unknown; enabled?: boolean | null }

type Props = {
  items: KindItem[]
  /** A phone or Discord is set up, so "Your phone" can be chosen. */
  phoneAvailable: boolean
  /** The values to store for a kind; null removes the key, so the extension's choice applies. */
  onChange: (kind: string, change: Change) => void
  onUnmute: (kind: string) => void
}

const WHEN: Record<string, string> = {
  now: 'Right away, outside quiet hours',
  when_home: 'When you get home',
  when_leaving: 'As you leave',
  next_talk: 'Next time you talk to him',
  briefing: 'In the morning briefing',
  silent: 'Only show it on Talk',
}
const WHERE: Record<string, string> = {
  auto: 'Where you are',
  speakers: 'Speakers at home',
  phone: 'Your phone',
}
const WHERE_NOTES: Record<string, string> = { auto: 'Speakers at home, your phone when away' }
/** A value given per urgency level: one choice in the list, not editable here. */
const PER_LEVEL = '__per_level__'
/** The last When: not said at all. */
const OFF = '__off__'

const choiceOf = (value: unknown) => (typeof value === 'string' ? value : PER_LEVEL)

/** A choice of when or where, the extension's own marked "(default)" in the open list. onChange gets
 * what to store (null for the extension's own) and the option picked. */
function Pick({ label, value, declared, labels, notes, disabled, onChange }: {
  label: string
  value: unknown
  declared: unknown
  labels: Record<string, string>
  /** A muted line under an option. */
  notes?: Record<string, string>
  /** Why an option cannot be picked yet, as its muted line. */
  disabled?: (option: string) => string | undefined
  onChange: (value: unknown, option: string) => void
}) {
  const current = choiceOf(value)
  const own = choiceOf(declared)
  const options = Object.keys(labels)
  if (current === PER_LEVEL || own === PER_LEVEL) options.unshift(PER_LEVEL)
  const name = (o: string) => (o === PER_LEVEL ? 'Depends on how urgent' : labels[o])
  return (
    <Select value={current} onValueChange={(v) => onChange(v === own ? null : v, v)}>
      <SelectTrigger size="sm" aria-label={label} className="max-w-full">
        <SelectValue>{name(current)}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => {
          const why = disabled?.(o)
          const note = why ?? notes?.[o]
          return (
            <SelectItem key={o} value={o} disabled={why !== undefined}>
              <span className="flex flex-col">
                <span>
                  {name(o)}
                  {o === own ? ' (default)' : ''}
                </span>
                {note ? <span className="text-xs text-muted-foreground">{note}</span> : null}
              </span>
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}

/** An extension's kinds of speaking up: when each is said, or Off, and where (your choice over the
 * extension's), and muted or not. The title and description take the full width, the choices sit under them. */
export function KindList({ items, phoneAvailable, onChange, onUnmute }: Props) {
  return (
    <ul className="flex flex-col divide-y">
      {items.map((k) => (
        <li key={k.kind} className="flex flex-col gap-2 py-3">
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="font-medium">{k.title}</span>
            {k.description ? <span className="text-sm text-muted-foreground">{k.description}</span> : null}
            {k.mutedText ? (
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Badge variant="outline">
                  <BellOff />
                  {k.mutedText}
                </Badge>
                <Button type="button" variant="ghost" size="sm" onClick={() => onUnmute(k.kind)}>
                  Unmute
                </Button>
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Pick
              label={`When to say ${k.title}`}
              value={k.enabled ? k.deliver : OFF}
              declared={k.declaredDeliver}
              labels={{ ...WHEN, ...(k.requested ? { now: 'Right away, even in quiet hours' } : {}), [OFF]: 'Off' }}
              onChange={(deliver, option) =>
                onChange(k.kind, option === OFF ? { enabled: false } : k.enabled ? { deliver } : { enabled: null, deliver })
              }
            />
            {!k.enabled || k.deliver === 'silent' ? null : (
              <Pick
                label={`Where to say ${k.title}`}
                value={k.where}
                declared={k.declaredWhere}
                labels={WHERE}
                notes={WHERE_NOTES}
                disabled={(o) => (o === 'phone' && !phoneAvailable ? 'Set your phone under Reaching you first' : undefined)}
                onChange={(where) => onChange(k.kind, { where })}
              />
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}
