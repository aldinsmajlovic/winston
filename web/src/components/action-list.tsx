import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useConfirm } from '@/hooks/use-confirm'
import { cn } from '@/lib/utils'

export type Tier = 'read' | 'act' | 'restricted'

/** One action as the list draws it; pages map their own data (an extension's tools, Home Assistant's)
 * into this. */
export type ActionItem = {
  /** The tool's own name, its key in config. */
  name: string
  title: string
  /** One line for people; may be ''. */
  summary: string
  /** What the brain calls it, shown in Details only. */
  brainName: string
  /** What the brain is told about it, shown in Details only. */
  description: string
  declared: Tier
  /** In effect: the declared tier unless you changed it. */
  tier: Tier
  enabled: boolean
  /** From an MCP server or a package: the declared tier is only what it says about itself. */
  thirdParty: boolean
}

type Change = { enabled?: boolean | null; tier?: Tier | null }

type Props = {
  items: ActionItem[]
  /** Every row has a closed Details with what he is told (Home Assistant's: words from outside Winston). A
   * third party's rows always have it. */
  details?: boolean
  /** Listed without a Permission choice (Home Assistant's). */
  readOnly?: boolean
  /** Start with the group folded to its header. */
  folded?: boolean
  /** Where the actions come from: draws a group header with a fold, a summary and a link. */
  source?: { name: string; icon?: ReactNode; to?: string; off?: boolean }
  /** Who chose what each action asks for, named in the confirm ("Spotify marked it..."); the
   * source's name by default. */
  maker?: string
  /** The values to store for an action; null removes the key, so the declared choice applies. */
  onChange?: (name: string, change: Change) => void
}

type Permission = 'own' | 'ask' | 'off'

const PERMISSIONS: Record<Permission, string> = { own: 'On its own', ask: 'Asks you first', off: 'Off' }

/** A fold's toggle: its text lines up with the column, and an open fold does not look pressed. */
const FOLD = '-ml-3 text-muted-foreground aria-expanded:bg-transparent aria-expanded:text-muted-foreground'

function permission(item: ActionItem): Permission {
  if (!item.enabled) return 'off'
  return item.tier === 'restricted' ? 'ask' : 'own'
}

/** What the source chose. Read is never offered: it runs the same as act, only logged differently. */
function declaredPermission(item: ActionItem): Permission {
  return item.declared === 'restricted' ? 'ask' : 'own'
}

/** What a choice stores: a tier only where it differs from the declared one. A third party's action
 * is off until it is listed, so turning it on says so. */
function stored(item: ActionItem, choice: Permission): Change {
  const enabled = item.thirdParty ? true : null
  if (choice === 'off') return { enabled: false }
  if (choice === 'ask') return { enabled, tier: item.declared === 'restricted' ? null : 'restricted' }
  return { enabled, tier: item.declared === 'restricted' ? 'act' : null }
}

/** "Changes things", or for a third party what it says about itself. Restricted is what an MCP tool with no
 * hints or one marked destructive declares, and what a package may declare: not marked safe. */
function marker(item: ActionItem): string {
  if (!item.thirdParty) return item.declared === 'read' ? 'Only looks' : 'Changes things'
  if (item.declared === 'read') return 'Says it only looks'
  return item.declared === 'act' ? 'Says it changes things' : 'Not marked safe'
}

function plural(n: number, one: string): string {
  return `${n} ${one}${n === 1 ? '' : 's'}`
}

/** The total, then only the exceptions: "3 actions", "14 actions, 2 ask you first". */
function summarize(items: ActionItem[], readOnly?: boolean): string {
  const total = plural(items.length, 'action')
  if (readOnly) return `${total}, on their own`
  const ask = items.filter((i) => permission(i) === 'ask').length
  const off = items.filter((i) => permission(i) === 'off').length
  return `${total}${ask ? `, ${ask} ${ask === 1 ? 'asks' : 'ask'} you first` : ''}${off ? `, ${off} off` : ''}`
}

/** Every source's actions in one form: what each does, whether it only looks, and one Permission
 * choice (On its own, Asks you first, Off). Loosening what the source asked for confirms first. */
export function ActionList({ items, details, readOnly, folded, source, maker = source?.name, onChange }: Props) {
  const ask = useConfirm()
  const [open, setOpen] = useState(!folded)

  const choose = async (item: ActionItem, choice: Permission) => {
    if (choice === 'own' && item.declared === 'restricted') {
      const yes = await ask({
        title: `Let ${item.title} run without asking?`,
        lines: [
          item.thirdParty
            ? `${maker ?? 'Its maker'} does not say it is safe, so he asks you first. He will do it as soon as he decides to.`
            : `${maker ?? 'Its maker'} marked it as needing your OK. He will do it as soon as he decides to.`,
        ],
        choices: [{ label: 'Run without asking', value: 'yes' }],
      })
      if (!yes) return
    }
    onChange?.(item.name, stored(item, choice))
  }

  const list = (
    <ul className={cn('flex flex-col divide-y', source?.off && 'opacity-60')}>
      {items.map((item) => (
        <ActionRow key={item.name} item={item} details={details || item.thirdParty} readOnly={readOnly} onChoose={(choice) => void choose(item, choice)} />
      ))}
    </ul>
  )
  if (!source) return list
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Button type="button" variant="ghost" className={cn(FOLD, 'justify-start')} aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronRight className={cn('transition-transform', open && 'rotate-90')} />
          {source.icon}
          <span className="font-medium text-foreground">{source.name}</span>
        </Button>
        <span className="text-sm text-muted-foreground">{source.off ? 'Off' : summarize(items, readOnly)}</span>
        {source.to ? (
          <Button asChild variant="link" size="sm" className="ml-auto px-0">
            <Link to={source.to} aria-label={`Settings for ${source.name}`}>
              Settings
            </Link>
          </Button>
        ) : null}
      </div>
      {open ? list : null}
    </div>
  )
}

/** One action. With `details`, a quiet Details fold under its summary shows the name he sees and what he
 * is told: words from outside Winston that go into his prompt. */
function ActionRow({ item, details, readOnly, onChoose }: { item: ActionItem; details?: boolean; readOnly?: boolean; onChoose: (choice: Permission) => void }) {
  const [open, setOpen] = useState(false)
  const declared = declaredPermission(item)
  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:gap-4">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="font-medium">{item.title}</span>
        {item.summary ? <span className="text-sm text-muted-foreground">{item.summary}</span> : null}
        {details ? (
          <>
            <Button type="button" variant="ghost" size="sm" className={cn(FOLD, 'self-start')} aria-expanded={open} onClick={() => setOpen(!open)}>
              <ChevronRight className={cn('transition-transform', open && 'rotate-90')} />
              Details
            </Button>
            {open ? (
              <div className="flex flex-col gap-1 text-sm break-words text-muted-foreground">
                <span>Name he sees: {item.brainName}</span>
                <span>What he is told: {item.description}</span>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
      <div className="flex items-center justify-between gap-3 sm:justify-end">
        <span className="text-sm text-muted-foreground">{marker(item)}</span>
        {readOnly ? null : (
          <Select value={permission(item)} onValueChange={(v) => onChoose(v as Permission)}>
            <SelectTrigger size="sm" aria-label={`Permission for ${item.title}`}>
              <SelectValue>{PERMISSIONS[permission(item)]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(PERMISSIONS) as Permission[]).map((p) => (
                <SelectItem key={p} value={p}>
                  {PERMISSIONS[p]}
                  {p === declared ? ' (default)' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
    </li>
  )
}
