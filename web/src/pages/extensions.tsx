import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ChevronRight, CircleAlert, Copy, Hourglass, Plus, ShieldAlert, Trash2 } from 'lucide-react'
import { Link, Navigate, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { $api, type Config, type ExtensionView } from '@/api/client'
import { useConfig } from '@/api/config'
import { notWorking } from '@/api/status'
import { ActionList, type ActionItem, type Tier } from '@/components/action-list'
import { IconButton } from '@/components/icon-button'
import { KindList, type KindItem } from '@/components/kind-list'
import { LogDialog } from '@/components/log-dialog'
import { Page, PageSkeleton } from '@/components/page'
import { SaveBar, SaveControls } from '@/components/save-controls'
import { SearchField } from '@/components/search-field'
import { SecretField } from '@/components/secret-field'
import { SectionForm } from '@/components/section-form'
import { StateIcon } from '@/components/state-icon'
import { StatusLine } from '@/components/status-line'
import { SettingsPage } from '@/components/templates/settings-page'
import { ExtensionIcon } from '@/components/tool-line'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useConfirm } from '@/hooks/use-confirm'
import { useNow } from '@/hooks/use-now'
import { useRegion } from '@/hooks/use-region'
import { discardSecrets, setSecret, useSecretsDraft } from '@/hooks/use-secrets'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { capitalize, humanize, joinNames } from '@/lib/format'
import { deepEqual, getIn, hasFields, section, setIn, type JsonSchema } from '@/lib/schema'
import { cn } from '@/lib/utils'

const EXTENSIONS = ['extensions']
const ID = /^[a-z][a-z0-9_]{0,39}$/
const VARIABLE = /^[A-Z][A-Z0-9_]{1,63}$/
const schemaKey = $api.queryOptions('get', '/api/config/schema').queryKey
const listKey = $api.queryOptions('get', '/api/extensions').queryKey

const STATE = {
  running: 'healthy',
  starting: 'starting',
  restarting: 'starting',
  stopped: 'stopped',
  failed: 'error',
  problem: 'error',
} as const

/** Where it comes from, on its page; built-in ones say nothing. */
const ORIGIN: Record<string, string> = {
  mcp: 'MCP server you added: runs as you on this computer',
  package: 'Package you installed: runs as you on this computer',
  personal: 'Your own, from the extensions folder: runs as you on this computer',
}
/** The same in a word or two, beside its name on its tile. */
const ORIGIN_WORD: Record<string, string> = { mcp: 'MCP server', package: 'Package', personal: 'Personal' }

/** A fold's toggle: its text lines up with the column, and an open fold does not look pressed. */
const FOLD = '-ml-3 text-muted-foreground aria-expanded:bg-transparent aria-expanded:text-muted-foreground'

type Region = ReturnType<typeof useRegion>
type Nudge = NonNullable<ExtensionView['nudges']>[number]
type Props = { e: ExtensionView; page: SettingsState }

/** "14:02" today, "Mon 19 Oct 08:00" on another day. */
function when(region: Region, t: number | Date): string {
  const d = typeof t === 'number' ? new Date(t * 1000) : t
  return d.toDateString() === new Date().toDateString() ? region.time(d) : `${region.day(d)} ${region.time(d)}`
}

const isOn = (config: Config, id: string) => getIn(config, [...EXTENSIONS, id, 'enabled']) === true
/** The address an MCP server was added by, or nothing for one started by a command. */
const mcpUrl = (page: SettingsState, id: string) => getIn(page.value, [...EXTENSIONS, id, 'mcp', 'url']) as string | null | undefined

/** "example.com:8000" from its address; the address itself when it does not parse. */
function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** The variable an MCP server's token goes in: one per server, by its id. */
const tokenVar = (id: string) => `${id.toUpperCase()}_MCP_TOKEN`

/** An MCP server added in the draft and not saved yet, as the list would show it once it is. */
function drafted(config: Config, id: string): ExtensionView | null {
  const mcp = getIn(config, [...EXTENSIONS, id, 'mcp']) as { title?: string | null } | null | undefined
  if (!mcp) return null
  const name = mcp.title || humanize(id)
  return { id, name, origin: 'mcp', target: '', enabled: false, state: 'stopped', icon: 'simple-icons:modelcontextprotocol' }
}

// Shared with the Actions and Speaking up pages: the extension list, and how its actions and messages map
// onto ActionList and KindList and write back into the draft.
/* eslint-disable react-refresh/only-export-components -- the shared mapping lives beside the page that owns it */

/** The extensions winstond found. A newly found one brings its settings into the config schema: read it again. */
export function useExtensions(schema: JsonSchema | undefined) {
  const client = useQueryClient()
  const list = $api.useQuery('get', '/api/extensions', undefined, { refetchInterval: 3000 })
  const ids = useMemo(() => (list.data ?? []).map((e) => e.id).join(','), [list.data])
  useEffect(() => {
    if (!schema || !ids) return
    const known = Object.keys(section(schema, EXTENSIONS).properties ?? {})
    if (ids.split(',').some((id) => !known.includes(id))) void client.invalidateQueries({ queryKey: schemaKey })
  }, [ids, schema, client])
  return list
}

type Choice = { enabled?: boolean | null; tier?: Tier | null; offer?: boolean | null }

/** Its actions as ActionList draws them, with the draft's choices. */
export function actionItems(e: ExtensionView, config: Config): ActionItem[] {
  const mcp = e.origin === 'mcp'
  return (e.tools ?? []).map((t) => {
    const choice = getIn(config, [...EXTENSIONS, e.id, 'tools', t.name]) as Choice | null | undefined
    return {
      name: t.name,
      title: t.title,
      summary: t.summary ?? '',
      brainName: t.brain_name,
      description: t.description,
      declared: t.declared_tier as Tier,
      tier: choice?.tier ?? (t.declared_tier as Tier),
      // An MCP server's action reaches him only once listed; an extension's unless switched off.
      enabled: mcp ? choice != null && choice.enabled !== false : choice?.enabled !== false,
      thirdParty: mcp || e.origin === 'package',
    }
  })
}

/** One action's or message's entry in the draft: null removes a key, so the extension's own choice
 * applies, and an entry left with nothing in it goes. On is the default, except for an MCP server's
 * action, which is on only while listed. */
function setEntry(page: SettingsState, at: string[], change: Record<string, unknown>, listed: boolean) {
  page.update((c) => {
    if (listed && change.enabled === false) return setIn(c, at, null)
    const next: Record<string, unknown> = { ...((getIn(c, at) as object | null) ?? {}), ...change }
    if (!listed && next.enabled === true) next.enabled = null
    const kept = Object.entries(next).filter(([, v]) => v != null)
    return setIn(c, at, kept.length ? Object.fromEntries(kept) : null)
  })
}

/** ActionList's onChange for one extension; also what sharing an action with other apps writes (offer). */
export function setAction(page: SettingsState, e: ExtensionView, name: string, change: Choice) {
  setEntry(page, [...EXTENSIONS, e.id, 'tools', name], change, e.origin === 'mcp')
}

/** Its kinds of message as KindList draws them, with the draft's choices. */
export function kindItems(e: ExtensionView, config: Config, region: Region): KindItem[] {
  return (e.kinds ?? []).map((k) => {
    const choice = getIn(config, [...EXTENSIONS, e.id, 'kinds', k.kind]) as { deliver?: unknown; where?: unknown; enabled?: boolean | null } | null | undefined
    const muted = k.muted_until
    return {
      kind: k.kind,
      title: k.title,
      description: k.description ?? '',
      requested: k.requested,
      deliver: choice?.deliver ?? k.declared_deliver,
      where: choice?.where ?? k.declared_where,
      declaredDeliver: k.declared_deliver,
      declaredWhere: k.declared_where,
      enabled: choice?.enabled !== false,
      mutedText: muted == null ? undefined : muted === 0 ? 'Muted' : `Muted until ${when(region, muted)}`,
    }
  })
}

/** KindList's onChange for one extension. */
export function setKind(page: SettingsState, e: ExtensionView, kind: string, change: Record<string, unknown>) {
  setEntry(page, [...EXTENSIONS, e.id, 'kinds', kind], change, false)
}

/** A phone or Discord is set up, so he can reach you away from home and ask there for your OK. */
export function canReachPhone(config: Config): boolean {
  return Boolean(config.speak_up?.phone) || config.channels?.discord?.enabled === true
}

/** Unmute one kind of message now (a command, not a setting). */
export function useUnmute() {
  const client = useQueryClient()
  const unmute = $api.useMutation('post', '/api/extensions/{ext_id}/kinds/{kind}/unmute', {
    onSuccess: () => client.invalidateQueries({ queryKey: listKey }),
  })
  return (extId: string, kind: string) => unmute.mutate({ params: { path: { ext_id: extId, kind } } })
}
/* eslint-enable react-refresh/only-export-components */

const COPY_FAILED = 'Could not copy. Select the text and copy it yourself.'

/** Text to paste elsewhere (an address, a command, an example) in a muted box, with Copy. */
export function CopyText({ text, label }: { text: string; label: string }) {
  const copy = () => {
    const done = navigator.clipboard?.writeText(text)
    if (!done) return void toast.error(COPY_FAILED)
    done.then(
      () => toast.success('Copied'),
      () => toast.error(COPY_FAILED),
    )
  }
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-0 flex-1 rounded-md bg-muted px-3 py-2 text-sm break-words whitespace-pre-wrap">{text}</div>
      <Button type="button" variant="outline" size="sm" aria-label={`Copy ${label}`} onClick={copy}>
        <Copy data-icon="inline-start" />
        Copy
      </Button>
    </div>
  )
}

const SAID: Record<string, string> = {
  speakers: 'Said on the speakers',
  phone: 'Sent to your phone',
  discord: 'Sent on Discord',
  conversation: 'Said in a conversation',
  silent: 'Shown on Talk',
}

/** Where a message stands, in a few words: whether and how it was said, or why not ("Held: quiet hours",
 * "Said on the speakers", "Not said: quiet hours" for one that ended unsaid). */
function standing(n: Nudge): string {
  const said = n.via ? (SAID[n.via.split(':')[0]] ?? 'Said') : n.state === 'said' ? 'Said' : null
  if (n.put_off) return `Put off until ${n.put_off}`
  if (n.state === 'held') return `Held: ${n.why ?? 'waiting'}`
  if (n.state === 'waiting') return n.why ? capitalize(n.why) : 'Waiting'
  if (n.state === 'dismissed') return said ? `${said}, then you dismissed it` : 'You dismissed it'
  return said ?? (n.why ? `Not said: ${n.why}` : 'Not said')
}

/** When a message shows it happened: when it was said, else when it came up. */
const shownAt = (n: Nudge) => new Date(n.said_at ?? n.seen_at)

/** The newest `max` messages he brought up, newest first: what he said, then its day, from which extension
 * and where it stands. */
export function NudgeList({ nudges, names, max }: { nudges: Nudge[]; names?: Record<string, string>; max: number }) {
  const region = useRegion()
  const newest = nudges.toSorted((a, b) => shownAt(b).getTime() - shownAt(a).getTime()).slice(0, max)
  return (
    <ul className="flex flex-col divide-y">
      {newest.map((n) => {
        const at = shownAt(n)
        return (
          <li key={n.id} className="flex flex-col gap-0.5 py-2 text-sm">
            <span>{n.say || n.title}</span>
            <span className="flex flex-wrap gap-x-3 text-muted-foreground">
              <span>{`${region.day(at)} ${region.time(at)}`}</span>
              {names ? <span>{names[n.extension] ?? n.extension}</span> : null}
              <span>{standing(n)}</span>
              {n.reason ? <span>You said: {n.reason}</span> : null}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

/** A key's label from its settings field ("Client secret"), else the variable it names. */
function keyLabel(page: SettingsState, e: ExtensionView, variable: string): string {
  const field = Object.entries(e.secrets ?? {}).find(([, v]) => v === variable)?.[0]
  try {
    const label = field ? (section(page.schema, [...EXTENSIONS, e.id, 'settings', field]).secret as { label?: string } | undefined)?.label : undefined
    return label ?? variable
  } catch {
    return variable
  }
}

/** Whether its form would draw a field, pruned as SectionForm prunes it: hidden ones do not count, and a
 * key whose variable's field is hidden does. */
function drawsFields(page: SettingsState, path: string[]): boolean {
  try {
    return hasFields(section(page.schema, path), page.schema, { config: page.value, path })
  } catch {
    return false
  }
}

const DAY = 24 * 3600

/** Its switch and whether it works: origin, live status, problems, and Remove for one you added. */
function About({ e, page }: Props) {
  const region = useRegion()
  const now = useNow(60_000)
  const ask = useConfirm()
  const navigate = useNavigate()
  const { pending } = useSecretsDraft()
  const on = isOn(page.value, e.id)
  const reachesNothing = e.origin !== 'mcp' && !(e.reach ?? []).length
  const help = on ? (e.enabled ? null : 'Starts when you save.') : e.enabled ? 'Stops when you save.' : reachesNothing ? null : 'When on, it can reach what is listed below.'
  const exits = e.exits ?? []
  const lastExit = exits.at(-1)
  const lastError = (e.errors ?? []).filter((x) => now / 1000 - x.at < DAY).at(-1)
  const keys = (e.needs ?? []).filter((v) => !(v in pending)).map((v) => keyLabel(page, e, v))
  const nothingOn = e.origin === 'mcp' && (e.tools ?? []).length > 0 && !actionItems(e, page.value).some((i) => i.enabled)
  const url = e.origin === 'mcp' ? mcpUrl(page, e.id) : null
  const origin = url ? `MCP server you added, at ${hostOf(url)}` : ORIGIN[e.origin]
  const switchId = `${e.id}-enabled`
  const removable = e.origin === 'mcp' || e.origin === 'personal'

  const remove = async () => {
    const yes = await ask({
      title: `Remove ${e.name}?`,
      lines: [
        e.origin === 'personal'
          ? 'Its settings go when you save. Its files stay in the extensions folder, so it shows here again, off.'
          : 'Its settings go when you save.',
      ],
      choices: [{ label: 'Remove', value: 'yes', destructive: true }],
    })
    if (!yes) return
    page.update((c) => setIn(c, [...EXTENSIONS, e.id], null))
    navigate('/extensions')
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-start gap-2 py-1">
          <Switch id={switchId} checked={on} onCheckedChange={(v) => page.update((c) => setIn(c, [...EXTENSIONS, e.id, 'enabled'], v))} />
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={switchId}>Use {e.name}</Label>
            {help ? <p className="text-sm text-muted-foreground">{help}</p> : null}
          </div>
        </div>
        {origin ? (
          <p className="flex items-center gap-2 text-muted-foreground">
            <ShieldAlert className="size-4 shrink-0" />
            {origin}
          </p>
        ) : null}
        {reachesNothing ? <p className="text-muted-foreground">Reaches nothing outside itself.</p> : null}
        {e.enabled && STATE[e.state as keyof typeof STATE] === 'starting' ? <StateIcon state="starting" detail={e.detail} /> : null}
        {e.status && e.state === 'running' ? <p>{e.status}</p> : null}
        {e.status_note && e.state === 'running' ? <p className="text-muted-foreground">{e.status_note}</p> : null}
        {e.status_warning && e.state === 'running' ? <StatusLine tone="warning">{e.status_warning}</StatusLine> : null}
        {e.problem ? (
          <StatusLine tone="error">{e.problem}</StatusLine>
        ) : e.state === 'failed' ? (
          <StatusLine tone="error">{e.detail ? `Stopped: ${e.detail}` : 'Stopped'}</StatusLine>
        ) : e.enabled && lastExit ? (
          <StatusLine tone={e.started_at ? 'muted' : 'warning'}>
            {`${e.started_at ? `Running again since ${when(region, e.started_at)}. ` : ''}Stopped ${exits.length === 1 ? 'once' : `${exits.length} times`} lately, last at ${when(region, lastExit.at)}: ${lastExit.why}`}
          </StatusLine>
        ) : e.enabled && e.state === 'stopped' ? (
          <StatusLine tone="warning">On, but not running.</StatusLine>
        ) : null}
        {keys.length ? <StatusLine tone={on ? 'warning' : 'muted'}>{`Needs its ${keys.length === 1 ? 'key' : 'keys'}: ${joinNames(keys)}`}</StatusLine> : null}
        {e.enabled && lastError ? <StatusLine tone="warning">{`Something it ran failed at ${when(region, lastError.at)}: ${lastError.error}`}</StatusLine> : null}
        {nothingOn ? <StatusLine tone="muted">Choose which of its actions he may use. Nothing is on yet.</StatusLine> : null}
        {removable ? (
          <Button variant="outline" size="sm" className="self-start" onClick={() => void remove()}>
            Remove this extension
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )
}

/** What its permissions let it do, what it can change first; an MCP server declares nothing. One that
 * reaches nothing says so on About instead. */
function Reach({ e, page }: Props) {
  const [shown, setShown] = useState(false)
  const reach = e.reach ?? []
  if (e.origin !== 'mcp' && !reach.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>What it can reach</CardTitle>
      </CardHeader>
      <CardContent>
        {e.origin === 'mcp' ? (
          <p>
            {mcpUrl(page, e.id)
              ? 'It does not say. It can do whatever that server does with what he sends it.'
              : 'It does not say. It runs as you and can do anything you can.'}
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {reach.map((r) => (
              <li key={r.text} className="flex flex-col gap-0.5">
                <span>{r.text}</span>
                {(r.hosts ?? []).length > 1 ? (
                  <>
                    <Button type="button" variant="ghost" size="sm" className={cn(FOLD, 'self-start')} aria-expanded={shown} onClick={() => setShown(!shown)}>
                      <ChevronRight className={cn('transition-transform', shown && 'rotate-90')} />
                      Show addresses
                    </Button>
                    {shown ? <span className="text-muted-foreground">{(r.hosts ?? []).join(', ')}</span> : null}
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/** The keys a server started by a command reads, each by the name it reads it by, with a field to paste it.
 * A name added here goes into the draft, so the key pasted next saves with the page. */
function McpKeys({ e, page, env }: Props & { env: string[] }) {
  const [name, setName] = useState('')
  const write = (next: string[]) => page.update((c) => setIn(c, [...EXTENSIONS, e.id, 'mcp', 'env'], next))
  const quiet = !isOn(page.value, e.id)
  return (
    <div className="flex flex-col gap-3">
      <span className="text-sm font-medium">Keys it needs</span>
      {env.map((n) => (
        <div key={n} className="flex flex-col gap-1.5">
          {/* The trash sits on the name, apart from the field's own Remove, which clears the saved key. */}
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor={`${e.id}-key-${n}`}>{n}</Label>
            <IconButton
              label={`Remove ${n}`}
              onClick={() => {
                discardSecrets([n])
                write(env.filter((x) => x !== n))
              }}
            >
              <Trash2 />
            </IconButton>
          </div>
          <SecretField id={`${e.id}-key-${n}`} names={[n]} label={n} placeholder="Paste the key" quiet={quiet} />
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Input aria-label="Name it reads the key by" value={name} onChange={(ev) => setName(ev.target.value.trim())} placeholder="API_KEY" />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!VARIABLE.test(name) || env.includes(name)}
          onClick={() => {
            write([...env, name])
            setName('')
          }}
        >
          Add a key
        </Button>
      </div>
    </div>
  )
}

/** Its own settings; for an MCP server how it starts, drawing only the way that applies, and for one
 * started by a command the keys it reads. */
function Settings({ e, page }: Props) {
  const path = [...EXTENSIONS, e.id]
  if (e.origin === 'mcp') {
    const mcp = getIn(page.value, [...path, 'mcp']) as { url?: string | null; command?: string[] | null; env?: string[] | null } | null | undefined
    const byCommand = !mcp?.url && Boolean(mcp?.command?.length)
    const omit = mcp?.url ? ['command', 'env'] : byCommand ? ['url', 'token_env', 'env'] : ['env']
    return (
      <SectionForm page={page} path={[...path, 'mcp']} title="Settings" omit={omit}>
        {byCommand ? <McpKeys e={e} page={page} env={mcp?.env ?? []} /> : null}
      </SectionForm>
    )
  }
  if (!drawsFields(page, [...path, 'settings'])) return null
  return <SectionForm page={page} path={[...path, 'settings']} title="Settings" />
}

function Actions({ e, page }: Props) {
  const items = actionItems(e, page.value)
  if (!items.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>What he can do with {e.name}</CardTitle>
      </CardHeader>
      <CardContent>
        <ActionList items={items} maker={e.name} onChange={(name, change) => setAction(page, e, name, change)} />
      </CardContent>
    </Card>
  )
}

function SpeaksUp({ e, page }: Props) {
  const region = useRegion()
  const unmute = useUnmute()
  const items = kindItems(e, page.value, region)
  if (!items.length) return null
  const recent = (e.nudges ?? []).filter((n) => n.state !== 'ended')
  return (
    <Card>
      <CardHeader>
        <CardTitle>When it speaks up</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <KindList
          items={items}
          phoneAvailable={canReachPhone(page.value)}
          onChange={(kind, change) => setKind(page, e, kind, change)}
          onUnmute={(kind) => unmute(e.id, kind)}
        />
        {recent.length ? (
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold">Recently</h3>
            <NudgeList nudges={recent} max={5} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

function Readings({ e }: { e: ExtensionView }) {
  const values = (e.entities ?? []).flatMap((x) =>
    (x.values ?? []).map((v) => ({ key: `${x.key}:${v.entry ?? ''}`, name: v.entry ? x.name.replace('{entry}', v.entry) : x.name, value: v.value, unit: x.unit })),
  )
  if (!values.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>Readings</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {values.map((v) => (
          <div key={v.key} className="flex flex-wrap gap-x-2">
            <span className="text-muted-foreground">{v.name}</span>
            <span>
              {String(v.value ?? 'Unknown')}
              {v.unit ? ` ${v.unit}` : ''}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

/** What its share of every request does to his first word, by its size; null when too small to notice. */
const slows = (chars: number) => (chars >= 5000 ? 'slows his first word noticeably' : chars >= 1000 ? 'slows his first word a little' : null)

/** Everything in extensions.<id> but its memory limit, for the limit's own form. */
const NOT_MEMORY = ['enabled', 'settings', 'kinds', 'tools', 'mcp']

/** When it runs next, what it costs every request, its memory while it runs, and its memory limit. An MCP
 * server over HTTP runs elsewhere, so it has no memory limit here. */
function HowItRuns({ e, page }: Props) {
  const region = useRegion()
  const next = Math.min(...(e.jobs ?? []).map((j) => j.next_run ?? Infinity))
  const slow = slows(e.prompt_chars ?? 0)
  const remote = e.origin === 'mcp' && !!mcpUrl(page, e.id)
  const lines = [
    Number.isFinite(next) ? `Runs again at ${when(region, next)}` : null,
    e.prompt_chars ? `Adds ${e.prompt_chars.toLocaleString()} characters to every request, ${slow ? `which ${slow}` : 'too few to notice'}` : null,
    e.state === 'running' && e.memory_mb ? `Uses ${Math.round(e.memory_mb)} MB of its ${e.memory_limit_mb} MB limit` : null,
  ].filter((l) => l !== null)
  if (remote && !lines.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>How it runs</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {lines.map((l) => (
          <p key={l}>{l}</p>
        ))}
        {!remote && <SectionForm page={page} path={[...EXTENSIONS, e.id]} omit={NOT_MEMORY} />}
      </CardContent>
    </Card>
  )
}

/** One extension's page: what it is and whether it works, what it can reach, its settings, what he can do
 * with it, when it speaks up, what it reads and how it runs. Sections with nothing to show are left out. */
export function ExtensionPage() {
  const { id = '' } = useParams()
  const s = useSettings()
  const list = useExtensions(s.page?.schema)
  const saved = useConfig().data
  const [log, setLog] = useState(false)
  const lines = $api.useQuery('get', '/api/extensions/{ext_id}/log', { params: { path: { ext_id: id }, query: { lines: 300 } } }, { enabled: log })
  const restart = $api.useMutation('post', '/api/extensions/{ext_id}/restart')
  if (s.loading || !list.data) return <PageSkeleton title="Extensions" />
  const { page } = s
  const found = list.data.find((x) => x.id === id)
  const e = found ?? drafted(page.value, id)
  // Removed in the draft: leave before its forms draw again and write their part of it back.
  if (!e || (found && getIn(saved, [...EXTENSIONS, id]) != null && getIn(page.value, [...EXTENSIONS, id]) == null)) return <Navigate to="/extensions" replace />
  return (
    <SettingsPage
      title={e.name}
      back={{ to: '/extensions', label: 'Extensions' }}
      intro={e.description || undefined}
      commands={
        found
          ? [
              { label: 'Log', onClick: () => setLog(true) },
              ...(e.enabled
                ? [
                    {
                      label: `${e.state === 'stopped' || e.state === 'failed' ? 'Start' : 'Restart'} ${e.name}`,
                      onClick: () => restart.mutate({ params: { path: { ext_id: e.id } } }),
                      disabled: restart.isPending,
                    },
                  ]
                : []),
            ]
          : undefined
      }
      page={page}
    >
      <About e={e} page={page} />
      <Reach e={e} page={page} />
      <Settings e={e} page={page} />
      <Actions e={e} page={page} />
      <SpeaksUp e={e} page={page} />
      <Readings e={e} />
      <HowItRuns e={e} page={page} />
      <LogDialog title={`Log of ${e.name}`} open={log} onOpenChange={setLog} lines={lines.data?.lines} loading={lines.isLoading} />
    </SettingsPage>
  )
}

/** "My computer" -> "my_computer": the id its actions are named by. */
const idFor = (name: string) =>
  name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)

/** "mcp_1", or the next number no extension has: the id for a name with no letter from a to z. */
function nextFreeMcpId(taken: string[]): string {
  let n = 1
  while (taken.includes(`mcp_${n}`)) n++
  return `mcp_${n}`
}

/** A command line as its words, keeping quoted ones whole. */
const words = (line: string) => line.match(/"[^"]*"|'[^']*'|\S+/g)?.map((a) => a.replace(/^["']|["']$/g, '')) ?? []

type KeyRow = { name: string; value: string }

function FieldError({ text }: { text: string | null }) {
  return text ? <p className="text-sm text-destructive">{text}</p> : null
}

/** An MCP server added as an extension of its own: the name it is shown by (its id follows), how it
 * starts, and the keys it reads or, for an address, its token. It goes into the draft switched on with no
 * action on yet; Save starts it and writes the keys. */
function AddMcp({ page, taken, open, onOpenChange, onAdded }: {
  page: SettingsState
  taken: string[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onAdded: (id: string) => void
}) {
  const [name, setName] = useState('')
  const [how, setHow] = useState<'command' | 'url'>('command')
  const [start, setStart] = useState('')
  const [keys, setKeys] = useState<KeyRow[]>([])
  const [token, setToken] = useState('')
  const [tried, setTried] = useState(false)
  const derived = idFor(name)
  const id = derived || (name.trim() ? nextFreeMcpId(taken) : '')
  const url = how === 'url'
  const nameError = !name.trim()
    ? 'Give it a name.'
    : !ID.test(id)
      ? 'Start the name with a letter from a to z.'
      : taken.includes(id)
        ? 'Another extension already has this name.'
        : null
  const startError = !start.trim()
    ? url
      ? 'Give its address.'
      : 'Give the command that starts it.'
    : url && !/^https?:\/\/\S+$/.test(start.trim())
      ? 'An address starts with http:// or https://.'
      : null
  const keyErrors = keys.map((k) =>
    k.name && !VARIABLE.test(k.name) ? 'Capital letters, digits and _, starting with a letter.' : !k.name && k.value ? 'Give the name it reads this key by.' : null,
  )
  const setKey = (i: number, change: Partial<KeyRow>) => setKeys(keys.map((k, j) => (j === i ? { ...k, ...change } : k)))

  const add = () => {
    setTried(true)
    // Keys go only to a server Winston starts, so an address takes none.
    if (nameError || startError || (!url && keyErrors.some(Boolean))) return
    const mcp = {
      title: name.trim(),
      ...(url ? { url: start.trim(), token_env: tokenVar(id) } : { command: words(start), env: keys.filter((k) => k.name).map((k) => k.name) }),
    }
    page.update((c) => setIn(c, [...EXTENSIONS, id], { enabled: true, mcp }))
    if (!url) for (const k of keys) if (k.name && k.value) setSecret(k.name, k.value)
    if (url && token) setSecret(tokenVar(id), token)
    setName('')
    setStart('')
    setKeys([])
    setToken('')
    setTried(false)
    onOpenChange(false)
    onAdded(id)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an MCP server</DialogTitle>
          <DialogDescription>
            MCP is how AI apps share actions. A server's actions can become his. A server you start runs as you on this computer, not in a sandbox.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-name">Name</Label>
            <Input id="mcp-name" value={name} onChange={(ev) => setName(ev.target.value)} placeholder="My computer" aria-invalid={tried && !!nameError} />
            <FieldError text={tried || name ? nameError : null} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-how">How it starts</Label>
            <Select value={how} onValueChange={(v) => setHow(v as 'command' | 'url')}>
              <SelectTrigger id="mcp-how" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="command">A command on this computer</SelectItem>
                <SelectItem value="url">An address</SelectItem>
              </SelectContent>
            </Select>
            <Input
              aria-label={url ? 'Address' : 'Command'}
              value={start}
              onChange={(ev) => setStart(ev.target.value)}
              placeholder={url ? 'https://' : 'npx -y some-mcp'}
              aria-invalid={tried && !!startError}
            />
            <FieldError text={tried || start ? startError : null} />
          </div>
          {url ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="mcp-token">Token</Label>
              <Input
                id="mcp-token"
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                value={token}
                onChange={(ev) => setToken(ev.target.value.trim())}
                placeholder="Optional"
              />
              <p className="text-sm text-muted-foreground">Only if the server gave you one.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Keys it needs</span>
              <p className="text-sm text-muted-foreground">The name its instructions give each key, like API_KEY, and the key itself. Kept on this computer.</p>
              {keys.map((k, i) => (
                <div key={i} className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <Input aria-label="Name it reads the key by" value={k.name} onChange={(ev) => setKey(i, { name: ev.target.value.trim() })} placeholder="API_KEY" />
                    <Input
                      aria-label="Key"
                      type="password"
                      autoComplete="new-password"
                      spellCheck={false}
                      value={k.value}
                      onChange={(ev) => setKey(i, { value: ev.target.value.trim() })}
                      placeholder="Paste the key"
                    />
                    <IconButton label="Remove this key" onClick={() => setKeys(keys.filter((_, j) => j !== i))}>
                      <Trash2 />
                    </IconButton>
                  </div>
                  <FieldError text={keyErrors[i]} />
                </div>
              ))}
              <Button type="button" variant="ghost" size="sm" className="-ml-2 self-start" onClick={() => setKeys([...keys, { name: '', value: '' }])}>
                <Plus data-icon="inline-start" />
                Add a key
              </Button>
            </div>
          )}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button onClick={add}>Add</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** One extension in the store: icon, name and where it comes from, two lines of what it does, and a word
 * only when there is something to say (a problem, a cost worth noticing). The whole tile opens its page,
 * where its reach shows before it can be switched on. */
function Tile({ e, page, changed }: Props & { changed: boolean }) {
  const why = e.problem || e.detail
  const on = isOn(page.value, e.id)
  const problem = (e.needs ?? []).length ? 'Needs a key' : on && notWorking(e) ? (e.state === 'stopped' ? 'Not running' : why ? `Stopped: ${why}` : 'Stopped') : null
  const origin = ORIGIN_WORD[e.origin]
  const cost = slows(e.prompt_chars ?? 0)
  return (
    <Card className="relative transition-colors hover:bg-muted/50 has-[a:focus-visible]:ring-2 has-[a:focus-visible]:ring-ring">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ExtensionIcon name={e.icon} />
          <Link to={`/extensions/${e.id}`} className="min-w-0 truncate outline-none after:absolute after:inset-0">
            {e.name}
          </Link>
          {origin ? (
            <Badge variant="outline" className="font-normal text-muted-foreground">
              {origin}
            </Badge>
          ) : null}
          {changed ? <span role="img" aria-label="Unsaved changes" className="ml-auto size-2 shrink-0 rounded-full bg-primary" /> : null}
        </CardTitle>
      </CardHeader>
      {e.description || problem || cost ? (
        <CardContent className="flex flex-col gap-2">
          {e.description ? <p className="line-clamp-2 text-muted-foreground">{e.description}</p> : null}
          {problem || cost ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
              {problem ? (
                <span className="flex min-w-0 items-center gap-1.5">
                  <CircleAlert className={cn('size-4 shrink-0', on && 'text-warning')} />
                  <span className="truncate">{problem}</span>
                </span>
              ) : null}
              {cost ? (
                <span className="flex items-center gap-1.5">
                  <Hourglass className="size-4 shrink-0" />
                  {capitalize(cost)}
                </span>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  )
}

function Group({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  if (!count) return null
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-medium">
        {title} <span className="text-muted-foreground">{count}</span>
      </h2>
      <div className="grid gap-3 sm:auto-rows-fr sm:grid-cols-2 lg:grid-cols-3">{children}</div>
    </section>
  )
}

/** The store: every extension as a tile in Needs you, On and Off, with a search over names, descriptions
 * and action titles; each opens its own page. It has no fields of its own. */
export default function ExtensionsPage() {
  const s = useSettings()
  const list = useExtensions(s.page?.schema)
  const saved = useConfig().data
  const navigate = useNavigate()
  const [adding, setAdding] = useState(false)
  const [search, setSearch] = useState('')
  if (s.loading || !list.data) return <PageSkeleton title="Extensions" />
  const { page } = s
  const newOnes = Object.keys(page.value.extensions ?? {})
    .filter((id) => !list.data.some((e) => e.id === id))
    .map((id) => drafted(page.value, id))
    .filter((e) => e !== null)
  const q = search.trim().toLowerCase()
  const hit = (e: ExtensionView) => !q || [e.name, e.id, e.description, ...(e.tools ?? []).map((t) => t.title)].some((w) => w?.toLowerCase().includes(q))
  const shown = [...list.data, ...newOnes].filter(hit).sort((a, b) => a.name.localeCompare(b.name))
  const changed = (e: ExtensionView) => !deepEqual(getIn(saved, [...EXTENSIONS, e.id]) ?? null, getIn(page.value, [...EXTENSIONS, e.id]) ?? null)
  const needsYou = (e: ExtensionView) => isOn(page.value, e.id) && (notWorking(e) || (e.needs ?? []).length > 0)
  const groups = [
    { title: 'Needs you', items: shown.filter(needsYou) },
    { title: 'On', items: shown.filter((e) => isOn(page.value, e.id) && !needsYou(e)) },
    { title: 'Off', items: shown.filter((e) => !isOn(page.value, e.id)) },
  ]
  const chars = list.data.reduce((n, e) => n + (e.prompt_chars ?? 0), 0)
  return (
    <Page
      title="Extensions"
      intro="Extensions add things he can do and know, and each shows what it can reach before you switch it on."
      commands={[{ label: 'Add an MCP server', icon: Plus, onClick: () => setAdding(true) }]}
      actions={<SaveControls page={page} />}
      footer={page.dirty && <SaveBar page={page} />}
    >
      <SearchField value={search} onChange={setSearch} placeholder="Search extensions" />
      {!list.data.length && !newOnes.length ? (
        <p className="text-muted-foreground">No extensions found. Personal ones go in the extensions folder of Winston's data folder.</p>
      ) : !shown.length ? (
        <p className="text-muted-foreground">Nothing matches.</p>
      ) : null}
      {groups.map((g) => (
        <Group key={g.title} title={g.title} count={g.items.length}>
          {g.items.map((e) => (
            <Tile key={e.id} e={e} page={page} changed={changed(e)} />
          ))}
        </Group>
      ))}
      {chars ? (
        <p className="text-sm text-muted-foreground">
          Extensions that are on add {chars.toLocaleString()} characters to every request. More means a slower first word.
        </p>
      ) : null}
      <AddMcp
        page={page}
        taken={[...list.data.map((e) => e.id), ...newOnes.map((e) => e.id)]}
        open={adding}
        onOpenChange={setAdding}
        onAdded={(id) => navigate(`/extensions/${id}`)}
      />
    </Page>
  )
}
