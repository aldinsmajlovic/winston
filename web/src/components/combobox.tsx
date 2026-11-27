import { useState, type ReactNode } from 'react'
import { ChevronDown, CircleAlert, LoaderCircle } from 'lucide-react'
import { KindIcon } from '@/components/kind-icon'
import { Button } from '@/components/ui/button'
import type { ModelFacts } from '@/api/client'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
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
  /** Found by the server for the typed text: shown as is, not filtered again. */
  searched?: boolean
  /** The brain's models: what the model picker shows of each. */
  facts?: ModelFacts | null
}

/** What a rich row can do besides being picked: type a search (a repo's sizes), or pick a value of its own. */
export type RowActions = { search: (text: string) => void; pick: (value: string) => void }

/** A toggle beside the dialog's search that keeps only the choices it holds for ("Fits"). */
export type Filter = { label: string; keep: (choice: Choice) => boolean }

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
  /** The typed text, for a server that searches further (its finds come back `searched`). */
  onSearch?: (text: string) => void
  /** Each choice drawn as a rich row, in a wide dialog (`title`, `description`) instead of a dropdown. */
  row?: (choice: Choice, act: RowActions) => ReactNode
  title?: string
  description?: ReactNode
  /** The dialog's filters, offered while they apply; toggled on together, a choice must pass each. */
  filters?: Filter[]
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
 * value that is not listed can be used as is. With `row`, a dialog of rich rows instead. */
export function Combobox({ id, value, choices: all, onChange, custom, loading, problem, disabled, placeholder, onClear, onSearch, row, title, description, filters = [] }: Props) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [on, setOn] = useState<string[]>([])
  // In the dialog a row is only chosen, by click or Enter, and Use takes it: a model costs a download and a restart.
  const [chosen, setChosen] = useState('')
  const choices = all.filter((c) => filters.every((f) => !on.includes(f.label) || f.keep(c)))
  const current = all.find((c) => c.value === value)
  const typed = search.trim()
  // cmdk leaves forceMount items out of its count, so it would hide their group and say Nothing matches.
  const found = choices.some((c) => c.searched)
  // A pasted link is the server's to read, not a value to use as is.
  const offerTyped = custom && typed !== '' && !typed.includes('://') && !choices.some((c) => c.value === typed)
  const type = (text: string) => {
    setSearch(text)
    onSearch?.(text)
  }
  const show = (o: boolean) => {
    setOpen(o)
    setChosen(value ?? '')
    if (!o) type('')
  }
  const take = (v: string) => {
    onChange(v)
    show(false)
  }
  const pick = row ? setChosen : take
  const trigger = (
    <Button
      id={id}
      type="button"
      variant="outline"
      role="combobox"
      aria-expanded={open}
      disabled={disabled}
      className="w-full justify-between border-input bg-transparent font-normal hover:bg-transparent"
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
  )
  const list = (
    <Command filter={filter} className={cn(row && 'min-h-0 flex-1 rounded-none! bg-transparent')}>
      {all.length >= SEARCH_FROM || custom ? (
        <div className={cn(row && 'flex flex-col gap-2 p-3 sm:flex-row sm:items-center')}>
          <div className="min-w-0 flex-1">
            <CommandInput placeholder={onSearch ? 'Search, or paste a link' : custom ? 'Search, or type a name' : 'Search'}
              value={search} onValueChange={type} busy={!!loading && !!typed} />
          </div>
          {row && filters.length ? (
            <div role="group" aria-label="Show only" className="flex gap-1">
              {filters.map((f) => (
                <Button key={f.label} type="button" variant="ghost" size="sm" aria-pressed={on.includes(f.label)} className="aria-pressed:bg-accent"
                  onClick={() => setOn((o) => (o.includes(f.label) ? o.filter((x) => x !== f.label) : [...o, f.label]))}>
                  {f.label}
                </Button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      <CommandList className={cn(row && 'max-h-none flex-1 px-2')}>
        {found ? null : (
          <CommandEmpty>
            {loading ? (
              <span className="inline-flex items-center gap-2 text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
                {typed ? 'Searching' : 'Loading'}
              </span>
            ) : problem ?? 'Nothing matches.'}
          </CommandEmpty>
        )}
        {onClear && value ? (
          <CommandGroup>
            <CommandItem
              value="none"
              onSelect={() => {
                onClear()
                show(false)
              }}
            >
              <span className="text-muted-foreground">None</span>
            </CommandItem>
          </CommandGroup>
        ) : null}
        {groups(choices).map(([group, items]) => (
          <CommandGroup key={group} heading={group || undefined} forceMount={items.some((c) => c.searched)}
            className={cn(row && '[&+&]:border-t **:[[cmdk-group-heading]]:pt-3 **:[[cmdk-group-heading]]:text-sm **:[[cmdk-group-heading]]:text-foreground')}>
            {items.map((c) => (
              <CommandItem
                key={c.value}
                forceMount={c.searched}
                value={`${c.label} ${c.value}`}
                data-checked={c.value === (row ? chosen : value)}
                disabled={c.disabled}
                onSelect={() => pick(c.value)}
              >
                {row ? row(c, { search: type, pick }) : (
                  <>
                    <ChoicePicture choice={c} className="size-6" />
                    <ChoiceName text={c.label} className="flex-1 truncate" />
                    {c.hint ? <span className="max-w-1/3 truncate text-xs text-muted-foreground">{c.hint}</span> : null}
                  </>
                )}
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
  )
  if (row) {
    const changed = !!chosen && chosen !== value
    return (
      <Dialog open={open} onOpenChange={show}>
        <DialogTrigger asChild>{trigger}</DialogTrigger>
        <DialogContent className="flex h-[min(48rem,calc(100dvh-2rem))] flex-col gap-0 p-0 sm:max-w-3xl">
          <DialogHeader className="border-b p-4 pr-12">
            <DialogTitle>{title}</DialogTitle>
            {description ? <DialogDescription>{description}</DialogDescription> : null}
          </DialogHeader>
          {list}
          <div className="flex flex-wrap items-center justify-end gap-2 border-t p-3">
            {changed ? (
              <span className="mr-auto min-w-0 truncate text-sm text-muted-foreground">
                {all.find((c) => c.value === chosen)?.label ?? chosen}
              </span>
            ) : null}
            <Button type="button" variant="outline" onClick={() => show(false)}>Cancel</Button>
            <Button type="button" variant={changed ? 'default' : 'outline'} disabled={!changed} onClick={() => take(chosen)}>
              Use this {title?.toLowerCase() ?? 'one'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    )
  }
  return (
    <Popover open={open} onOpenChange={show}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) p-0" align="start">
        {list}
      </PopoverContent>
    </Popover>
  )
}
