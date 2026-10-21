import { useState } from 'react'
import { ChevronDown, CircleAlert, LoaderCircle } from 'lucide-react'
import { KindIcon } from '@/components/kind-icon'
import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

export type Choice = {
  value: string
  label: string
  hint?: string | null
  icon?: string | null
  /** A cover, shown in place of the icon. */
  image?: string | null
  group?: string | null
  /** Listed but not pickable (greyed); the hint says why. */
  disabled?: boolean
}

type Props = {
  id?: string
  value: string | undefined
  choices: Choice[]
  onChange: (value: string) => void
  /** Accept a typed value that is not in the list. */
  custom?: boolean
  loading?: boolean
  /** Why the list is empty or incomplete, shown as a warning icon. */
  problem?: string
  disabled?: boolean
  placeholder?: string
  /** Offer a None entry that unsets the field. */
  onClear?: () => void
}

const SEARCH_FROM = 8

/** Every typed word must appear somewhere in the item; cmdk's fuzzy default matches too much. */
function filter(value: string, search: string): number {
  const text = value.toLowerCase()
  return search.toLowerCase().split(/\s+/).every((w) => text.includes(w)) ? 1 : 0
}

function groups(choices: Choice[]): [string, Choice[]][] {
  const out = new Map<string, Choice[]>()
  for (const c of choices) {
    const key = c.group ?? ''
    out.set(key, [...(out.get(key) ?? []), c])
  }
  return [...out.entries()]
}

/** A choice's cover when it has one, else its kind's icon; nothing in its place when it has neither. */
export function ChoicePicture({ choice, className }: { choice?: Choice; className: string }) {
  if (choice?.image) return <img src={choice.image} alt="" className={cn('shrink-0 rounded-sm object-cover', className)} />
  return <KindIcon name={choice?.icon} />
}

/** A choice's name; one made only of emoji (a playlist named with one) is drawn larger, so it reads like a word. */
export function ChoiceName({ text, className }: { text: string; className?: string }) {
  const emoji = /\p{Extended_Pictographic}/u.test(text) && !/[\p{L}\p{N}]/u.test(text)
  return <span className={cn(emoji && 'text-xl leading-6', className)}>{text}</span>
}

/** A dropdown of choices fetched from somewhere, searchable when long; with `custom` a typed
 * value that is not listed can be used as is. */
export function Combobox({ id, value, choices, onChange, custom, loading, problem, disabled, placeholder, onClear }: Props) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const current = choices.find((c) => c.value === value)
  const typed = search.trim()
  const offerTyped = custom && typed !== '' && !choices.some((c) => c.value === typed)
  const pick = (v: string) => {
    onChange(v)
    setOpen(false)
    setSearch('')
  }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="w-full justify-between font-normal"
        >
          <span className="flex min-w-0 items-center gap-2">
            <ChoicePicture choice={current} className="size-5" />
            <ChoiceName text={current?.label ?? (value || placeholder || 'Not set')} className={cn('truncate', !current && !value && 'text-muted-foreground')} />
          </span>
          <span className="flex items-center gap-2">
            {loading ? <LoaderCircle className="size-4 animate-spin text-muted-foreground" /> : null}
            {problem ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex">
                    <CircleAlert className="size-4 text-warning" aria-label={problem} />
                  </span>
                </TooltipTrigger>
                <TooltipContent>{problem}</TooltipContent>
              </Tooltip>
            ) : null}
            <ChevronDown className="size-4 text-muted-foreground" />
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) p-0" align="start">
        <Command filter={filter}>
          {choices.length >= SEARCH_FROM || custom ? (
            <CommandInput placeholder="Search" value={search} onValueChange={setSearch} />
          ) : null}
          <CommandList>
            <CommandEmpty>{problem ?? 'Nothing found'}</CommandEmpty>
            {onClear && value ? (
              <CommandGroup>
                <CommandItem
                  value="none"
                  onSelect={() => {
                    onClear()
                    setOpen(false)
                  }}
                >
                  <span className="text-muted-foreground">None</span>
                </CommandItem>
              </CommandGroup>
            ) : null}
            {groups(choices).map(([group, items]) => (
              <CommandGroup key={group} heading={group || undefined}>
                {items.map((c) => (
                  <CommandItem
                    key={c.value}
                    value={`${c.label} ${c.value}`}
                    data-checked={c.value === value}
                    disabled={c.disabled}
                    onSelect={() => pick(c.value)}
                  >
                    <ChoicePicture choice={c} className="size-6" />
                    <ChoiceName text={c.label} className="flex-1 truncate" />
                    {c.hint ? <span className="max-w-1/3 truncate text-xs text-muted-foreground">{c.hint}</span> : null}
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
            {offerTyped ? (
              <CommandGroup forceMount>
                <CommandItem forceMount value={`use ${typed}`} onSelect={() => pick(typed)}>
                  Use {typed}
                </CommandItem>
              </CommandGroup>
            ) : null}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
