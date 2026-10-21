import { useState } from 'react'
import { $api, errorText, type ExtensionView } from '@/api/client'
import { useConfig } from '@/api/config'
import { ActionList, type ActionItem } from '@/components/action-list'
import { Fold } from '@/components/fold'
import { KindIcon } from '@/components/kind-icon'
import { PageSkeleton } from '@/components/page'
import { SearchField } from '@/components/search-field'
import { StatusLine } from '@/components/status-line'
import { SettingsPage } from '@/components/templates/settings-page'
import { ExtensionIcon } from '@/components/tool-line'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { haActionItems } from '@/hooks/use-home-assistant'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { getIn } from '@/lib/schema'
import { actionItems, canReachPhone, CopyText, setAction, useExtensions } from '@/pages/extensions'

const EXTENSIONS = ['extensions']

type Filter = 'all' | 'ask' | 'off'
const FILTERS: Record<Filter, string> = { all: 'All', ask: 'Asks you first', off: 'Off' }
/** Whether an action shows under a filter; `on` is its group's, so an off extension's actions count as Off. */
const passes: Record<Filter, (item: ActionItem, on: boolean) => boolean> = {
  all: () => true,
  ask: (item, on) => on && item.enabled && item.tier === 'restricted',
  off: (item, on) => !on || !item.enabled,
}

type Group = { e: ExtensionView; items: ActionItem[]; on: boolean }

/** The address other apps reach him at over MCP, and which actions of the extensions that are on they may
 * use: each extension a closed fold, nothing shared until you tick it. */
function OtherApps({ groups, page }: { groups: Group[]; page: SettingsState }) {
  const address = `${location.origin}/mcp`
  return (
    <Card>
      <CardHeader>
        <CardTitle>Share with other AI apps</CardTitle>
        <CardDescription>
          AI apps on this computer that speak MCP can use the actions you share here; ones that ask you first still wait for your OK.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Address</span>
          <CopyText text={address} label="the address" />
        </div>
        {groups.filter((g) => g.on).map(({ e, items }) => {
          const shared = (name: string) => getIn(page.value, [...EXTENSIONS, e.id, 'tools', name, 'offer']) === true
          const count = items.filter((i) => i.enabled && shared(i.name)).length
          return (
            <Fold key={e.id} title={e.name} icon={<ExtensionIcon name={e.icon} />} summary={`${count} of ${items.length} shared`}>
              <ul className="flex flex-col divide-y">
                {items.map((i) => {
                  const off = !i.enabled
                  const id = `offer-${e.id}-${i.name}`
                  return (
                    <li key={i.name} className="flex items-center gap-3 py-2">
                      <Checkbox
                        id={id}
                        checked={!off && shared(i.name)}
                        disabled={off}
                        onCheckedChange={(v) => setAction(page, e, i.name, { offer: v === true || null })}
                      />
                      <Label htmlFor={id} className="font-normal">
                        {i.title}
                      </Label>
                      {off ? <span className="ml-auto text-sm text-muted-foreground">Off for him</span> : null}
                    </li>
                  )
                })}
              </ul>
            </Fold>
          )
        })}
      </CardContent>
    </Card>
  )
}

/** Everything he can do, from every source, and which actions wait for your OK: one list per extension
 * with its Permission choices, Home Assistant's read-only with what he is told in Details, and what other
 * apps may use. */
export default function ActionsPage() {
  const s = useSettings()
  const list = useExtensions(s.page?.schema)
  const saved = useConfig().data?.providers?.home_assistant
  const haOn = !!saved && saved.enabled !== false && !!saved.url
  const ha = $api.useQuery('get', '/api/providers/home_assistant/tools', undefined, { enabled: haOn, retry: false })
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  if (s.loading || !list.data) return <PageSkeleton title="Actions" />
  const { page } = s
  const q = search.trim().toLowerCase()
  // A search matches an action's words or its source's name, so "spotify" shows all of Spotify's.
  const keep = (item: ActionItem, on: boolean, source: string) =>
    passes[filter](item, on) && (!q || [item.title, item.summary, item.name, source].some((w) => w.toLowerCase().includes(q)))
  // Searching or filtering opens every group, so what matches shows.
  const open = Boolean(q) || filter !== 'all'
  const groups: Group[] = list.data
    .map((e) => ({ e, items: actionItems(e, page.value), on: getIn(page.value, [...EXTENSIONS, e.id, 'enabled']) === true }))
    .filter((g) => g.items.length)
    .sort((a, b) => Number(b.on) - Number(a.on) || a.e.name.localeCompare(b.e.name))
  const shown = groups.map((g) => ({ ...g, items: g.items.filter((i) => keep(i, g.on, g.e.name)) })).filter((g) => g.items.length)
  const haShown = haActionItems(ha.data ?? []).filter((i) => keep(i, true, 'Home Assistant'))
  return (
    <SettingsPage title="Actions" intro="Everything he can do, where it comes from, and which things need your OK first." page={page}>
      <div className="flex flex-col gap-3">
        <StatusLine tone="muted">
          When an action asks you first, he says what he wants to do and waits up to 5 minutes for your OK on the Talk page or your phone. Saying yes out loud is not
          enough.
        </StatusLine>
        {canReachPhone(page.value) ? null : (
          <StatusLine tone="warning" action={{ label: 'Set your phone', to: '/speaking-up' }}>
            No phone is set, so he can only ask on the Talk page.
          </StatusLine>
        )}
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <SearchField value={search} onChange={setSearch} placeholder="Search actions" />
        </div>
        <div role="group" aria-label="Show" className="flex gap-1">
          {(Object.keys(FILTERS) as Filter[]).map((f) => (
            <Button key={f} variant="ghost" size="sm" aria-pressed={filter === f} className="aria-pressed:bg-accent" onClick={() => setFilter(f)}>
              {FILTERS[f]}
            </Button>
          ))}
        </div>
      </div>
      <Card>
        <CardContent className="flex flex-col gap-4">
          {haOn && ha.isError ? (
            <StatusLine tone="warning" action={{ label: 'Open Home Assistant', to: '/home-assistant' }}>
              {errorText(ha.error)}
            </StatusLine>
          ) : null}
          {haShown.length ? (
            <ActionList
              key={`home_assistant:${open}`}
              items={haShown}
              details
              readOnly
              folded={!open}
              source={{ name: 'Home Assistant', icon: <KindIcon name="homeassistant" />, to: '/home-assistant' }}
            />
          ) : null}
          {shown.map(({ e, items, on }) => (
            <ActionList
              key={`${e.id}:${open}`}
              items={items}
              folded={!open}
              source={{ name: e.name, icon: <ExtensionIcon name={e.icon} />, to: `/extensions/${e.id}`, off: !on }}
              onChange={(name, change) => setAction(page, e, name, change)}
            />
          ))}
          {shown.length || haShown.length ? null : groups.length || haOn ? (
            <p className="text-muted-foreground">Nothing matches.</p>
          ) : (
            <StatusLine tone="muted" action={{ label: 'Open Extensions', to: '/extensions' }}>
              No actions yet. Each extension you switch on brings its own.
            </StatusLine>
          )}
        </CardContent>
      </Card>
      {groups.some((g) => g.on) ? <OtherApps groups={groups} page={page} /> : null}
    </SettingsPage>
  )
}
