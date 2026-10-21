import { useMemo, useState } from 'react'
import { CircleAlert, LoaderCircle } from 'lucide-react'
import { errorText } from '@/api/client'
import { KindIcon } from '@/components/kind-icon'
import { SearchField } from '@/components/search-field'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { components } from '@/api/schema'
import type { HAEntity } from '@/hooks/use-home-assistant'
import { humanize } from '@/lib/format'

type HARegistry = components['schemas']['HARegistry']

const ALL = 'all'

/** Home Assistant's own settings and diagnostics, and what it hides: rarely for him, so behind a tick. */
const tucked = (e: HAEntity) => e.hidden === true || e.category === 'config' || e.category === 'diagnostic'

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  registry: { data?: HARegistry; error: unknown; isLoading: boolean }
  /** Ids already on the list (the draft), which the picker leaves out. */
  listed: Set<string>
  onAdd: (entities: HAEntity[]) => void
  /** Add one by its entity id, for an entity the registry does not list. */
  onTypeId: () => void
}

/** Add from Home Assistant: its entities by room, with search and a type filter, ticked and added at once.
 * The page gives it a new `key` each time it opens, so it starts empty. */
export function HAEntityPicker({ open, onOpenChange, registry, listed, onAdd, onTypeId }: Props) {
  const [search, setSearch] = useState('')
  const [type, setType] = useState(ALL)
  const [showTucked, setShowTucked] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const data = registry.data

  const all = useMemo(() => (data?.groups ?? []).flatMap((g) => g.entities), [data])
  // What may be added: not on the list yet, and settings and diagnostics only when asked for.
  const addable = all.filter((e) => !listed.has(e.entity_id))
  const tuckedCount = addable.filter(tucked).length
  const candidates = showTucked ? addable : addable.filter((e) => !tucked(e))
  const counts = new Map<string, number>()
  for (const e of candidates) counts.set(e.domain, (counts.get(e.domain) ?? 0) + 1)
  const domains = [...counts.keys()].sort()
  // A type whose entities were all hidden again falls back to All.
  const kind = type === ALL || counts.has(type) ? type : ALL
  const offered = new Set(candidates.map((e) => e.entity_id))
  const q = search.trim().toLocaleLowerCase()
  const shown = (data?.groups ?? [])
    .map((g) => ({
      ...g,
      entities: g.entities.filter(
        (e) =>
          offered.has(e.entity_id) &&
          (kind === ALL || e.domain === kind) &&
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
          {g.name ?? (data.areas_known ? 'No room' : 'Devices')}
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
          <Select value={kind} onValueChange={setType}>
            <SelectTrigger aria-label="Type" className="sm:w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All ({candidates.length})</SelectItem>
              <SelectSeparator />
              {domains.map((d) => (
                <SelectItem key={d} value={d}>
                  <KindIcon name={d} />
                  {humanize(d)} ({counts.get(d)})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="-mx-2 max-h-[50vh] overflow-y-auto">{body}</div>
        {tuckedCount ? (
          <div className="flex items-center gap-2">
            <Checkbox id="pick-tucked" checked={showTucked} onCheckedChange={(on) => setShowTucked(on === true)} />
            <Label htmlFor="pick-tucked" className="font-normal">
              Show {tuckedCount} hidden (settings and diagnostics)
            </Label>
          </div>
        ) : null}
        <DialogFooter className="sm:justify-between">
          <Button variant="ghost" onClick={onTypeId}>
            Add by entity ID
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
