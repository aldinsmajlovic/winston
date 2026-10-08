import { useMemo, useState } from 'react'
import { CircleAlert, LoaderCircle } from 'lucide-react'
import { errorText } from '@/api/client'
import { KindIcon } from '@/components/kind-icon'
import { SearchField } from '@/components/search-field'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { components } from '@/api/schema'
import { NOISY_DOMAINS, type HAEntity } from '@/hooks/use-home-assistant'
import { humanize } from '@/lib/format'

type HARegistry = components['schemas']['HARegistry']

const COMMON = 'common'
const ALL = 'all'

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  registry: { data?: HARegistry; error: unknown; isLoading: boolean }
  /** Ids already on the list (the draft), which the picker leaves out. */
  listed: Set<string>
  onAdd: (entities: HAEntity[]) => void
  /** Add one by typing its id, for an entity the registry does not list. */
  onTypeId: () => void
}

/** Add from Home Assistant: its entities by room, with search and a type filter, ticked and added at once.
 * The page gives it a new `key` each time it opens, so it starts empty. */
export function HAEntityPicker({ open, onOpenChange, registry, listed, onAdd, onTypeId }: Props) {
  const [search, setSearch] = useState('')
  const [type, setType] = useState(COMMON)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const data = registry.data


  const all = useMemo(() => (data?.groups ?? []).flatMap((g) => g.entities), [data])
  const domains = useMemo(() => [...new Set(all.map((e) => e.domain))].sort(), [all])
  const q = search.trim().toLocaleLowerCase()
  const shown = (data?.groups ?? [])
    .map((g) => ({
      ...g,
      entities: g.entities.filter(
        (e) =>
          !listed.has(e.entity_id) &&
          (type === ALL ? true : type === COMMON ? !NOISY_DOMAINS.includes(e.domain) : e.domain === type) &&
          (!q ||
            [e.friendly_name, e.entity_id, e.device, e.area, ...e.aliases].some((h) => h?.toLocaleLowerCase().includes(q))),
      ),
    }))
    .filter((g) => g.entities.length)
  const byId = new Map(all.map((e) => [e.entity_id, e]))
  const toggle = (id: string, on: boolean) =>
    setPicked((p) => {
      const next = new Set(p)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  let body
  if (registry.isLoading) {
    body = <LoaderCircle className="m-4 size-4 animate-spin text-muted-foreground" aria-label="Loading" />
  } else if (!data?.ok) {
    body = (
      <div className="flex items-center gap-2 p-2 text-sm">
        <CircleAlert className="size-4 shrink-0 text-warning" />
        {data?.detail || errorText(registry.error)}
      </div>
    )
  } else if (!shown.length) {
    body = <div className="p-2 text-sm text-muted-foreground">Nothing to add.</div>
  } else {
    body = shown.map((g) => (
      <div key={g.area_id ?? ''} role="group" aria-label={g.name ?? 'No room'} className="flex flex-col">
        <div className="px-2 pt-3 pb-1 text-sm font-medium text-muted-foreground">
          {g.name ?? (data.areas_known ? 'No room' : 'Entities')}
        </div>
        {g.entities.map((e) => {
          const id = `pick-${e.entity_id}`
          return (
            <label key={e.entity_id} htmlFor={id} className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 hover:bg-muted">
              <Checkbox id={id} checked={picked.has(e.entity_id)} onCheckedChange={(on) => toggle(e.entity_id, on === true)} />
              <KindIcon name={e.domain} className="text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{e.friendly_name ?? e.entity_id}</span>
              <span className="shrink-0 text-sm text-muted-foreground">
                {[humanize(e.domain), e.state === 'unavailable' ? 'unavailable' : e.state_text].filter(Boolean).join(', ')}
              </span>
            </label>
          )
        })}
      </div>
    ))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add from Home Assistant</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="flex-1">
            <SearchField value={search} onChange={setSearch} placeholder="Search" label="Search Home Assistant" autoFocus />
          </div>
          <Select value={type} onValueChange={setType}>
            <SelectTrigger aria-label="Type" className="sm:w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={COMMON}>Common types</SelectItem>
              <SelectItem value={ALL}>All types</SelectItem>
              <SelectSeparator />
              {domains.map((d) => (
                <SelectItem key={d} value={d}>
                  <KindIcon name={d} />
                  {humanize(d)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="-mx-2 max-h-[50vh] overflow-y-auto">{body}</div>
        <DialogFooter className="sm:justify-between">
          <Button variant="ghost" onClick={onTypeId}>
            Type an id
          </Button>
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              disabled={picked.size === 0}
              onClick={() => {
                onAdd([...picked].flatMap((id) => byId.get(id) ?? []))
                onOpenChange(false)
              }}
            >
              Add {picked.size || ''}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
