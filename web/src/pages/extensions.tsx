import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { BellOff, CircleAlert, MemoryStick, MessageSquareText, Plus, RotateCw, ScrollText, ShieldAlert } from 'lucide-react'
import { DynamicIcon, iconNames, type IconName } from 'lucide-react/dynamic'
import { $api, type ExtensionView } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { StateIcon } from '@/components/state-icon'
import { StatusTip } from '@/components/status-tip'
import { SettingsPage } from '@/components/templates/settings-page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { capitalize } from '@/lib/format'
import { getIn, section, setIn } from '@/lib/schema'

const SPEAK_UP = ['speak_up']
const EXTENSIONS = ['extensions']
const ID = /^[a-z][a-z0-9_]{0,39}$/
const schemaKey = $api.queryOptions('get', '/api/config/schema').queryKey

const STATE = {
  running: 'healthy',
  starting: 'starting',
  restarting: 'starting',
  stopped: 'stopped',
  failed: 'error',
  problem: 'error',
} as const

const TIERS = {
  read: 'Read: runs',
  act: 'Act: runs, logged as an action',
  restricted: 'Restricted: waits for you to confirm',
} as const
type Tier = keyof typeof TIERS

const DELIVER = {
  now: 'As soon as allowed',
  when_home: 'When you are home',
  when_leaving: 'As you leave',
  next_talk: 'Next time you talk',
  briefing: 'Morning briefing',
  silent: 'Silently',
} as const
const WHERE = { auto: 'Where you are', speakers: 'Speakers', phone: 'Phone' } as const
const PER_LEVEL = 'per_level'

/** A nudge's standing in a few words: "Put off until ...", "Held: quiet hours", "Said on the speakers". */
function standing(n: NonNullable<ExtensionView['nudges']>[number]): string {
  if (n.put_off) return `Put off until ${n.put_off}`
  if (n.state === 'held') return `Held: ${n.why ?? 'waiting'}`
  if (n.state === 'waiting') return n.why ? capitalize(n.why) : 'Waiting'
  if (n.state === 'said') return `Said ${n.via === 'conversation' ? 'in a conversation' : n.via === 'silent' ? 'silently' : `on the ${(n.via ?? '').split(':')[0]}`}`
  if (n.state === 'dismissed') return 'Dismissed'
  return 'Ended'
}

const ORIGIN: Record<string, string> = { 'built-in': 'Built in', package: 'Package', personal: 'Personal', mcp: 'MCP server' }

/** What an extension may reach, in plain words, from its manifest. */
function reach(e: ExtensionView): string[] {
  const p = e.permissions as Record<string, unknown>
  const out: string[] = []
  const hosts = (p.network as string[] | undefined) ?? []
  if (hosts.includes('*')) out.push('Any website')
  else out.push(...hosts)
  if (p.home_assistant) out.push(p.home_assistant === 'act' ? 'Home Assistant: changes things' : 'Home Assistant: reads')
  if (p.memory) out.push(p.memory === 'write' ? 'Memory: reads and writes' : 'Memory: reads')
  if (p.presence) out.push('Whether you are home')
  if (p.llm) out.push('The background model')
  for (const other of (p.extensions as string[] | undefined) ?? []) out.push(`Tools of ${other}`)
  return out
}

function ExtensionIcon({ name }: { name: string }) {
  const icon = (iconNames as readonly string[]).includes(name) ? (name as IconName) : 'puzzle'
  return <DynamicIcon name={icon} className="size-4 shrink-0" />
}

function LogDialog({ id, open, onOpenChange }: { id: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const log = $api.useQuery('get', '/api/extensions/{ext_id}/log', { params: { path: { ext_id: id }, query: { lines: 300 } } }, { enabled: open })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Log of {id}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-auto rounded-md border p-3 text-xs/relaxed break-all whitespace-pre-wrap">
          {log.data?.lines.length ? log.data.lines.join('\n') : 'Nothing logged yet.'}
        </div>
      </DialogContent>
    </Dialog>
  )
}

type Props = { e: ExtensionView; page: SettingsState }

function Tools({ e, page }: Props) {
  const mcp = e.origin === 'mcp'
  const at = (tool: string, key: string) => [...EXTENSIONS, e.id, 'tools', tool, key]
  const set = (tool: string, key: string, value: unknown) => page.update((c) => setIn(c, at(tool, key), value))
  const tools = e.tools ?? []
  if (!tools.length) return null
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Tool</TableHead>
          <TableHead>Tier</TableHead>
          <TableHead>Brain</TableHead>
          <TableHead>Other apps</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tools.map((t) => {
          const saved = getIn(page.value, [...EXTENSIONS, e.id, 'tools', t.name]) as Record<string, unknown> | undefined
          const tier = (saved?.tier as Tier | undefined) ?? (t.declared_tier as Tier)
          // An MCP server's tool reaches the brain only when listed in config; an extension's unless switched off.
          const onForBrain = mcp ? saved !== undefined && saved.enabled !== false : saved?.enabled !== false
          const offer = saved?.offer === true
          return (
            <TableRow key={t.name}>
              <TableCell className="whitespace-normal">
                <span className="flex items-center gap-1.5">
                  {t.brain_name}
                  <InfoTip text={t.description} />
                </span>
              </TableCell>
              <TableCell>
                <Select value={tier} onValueChange={(v) => set(t.name, 'tier', v === t.declared_tier ? null : v)}>
                  <SelectTrigger size="sm" aria-label={`Tier of ${t.name}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.keys(TIERS) as Tier[]).map((k) => (
                      <SelectItem key={k} value={k}>
                        {TIERS[k]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </TableCell>
              <TableCell>
                <Switch
                  checked={onForBrain}
                  aria-label={`Offer ${t.name} to the brain`}
                  onCheckedChange={(v) =>
                    mcp
                      ? page.update((c) => setIn(c, [...EXTENSIONS, e.id, 'tools', t.name], v ? { ...(saved ?? {}), enabled: true } : null))
                      : set(t.name, 'enabled', v ? null : false)
                  }
                />
              </TableCell>
              <TableCell>
                <Switch checked={offer} aria-label={`Offer ${t.name} to other apps`} onCheckedChange={(v) => set(t.name, 'offer', v || null)} />
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

/** Its kinds of speaking up: when and where each is said (your choice over the extension's), on or
 * off, muted or not; then its nudges and where each stands. */
function SpeakingUp({ e, page }: Props) {
  const unmute = $api.useMutation('post', '/api/extensions/{ext_id}/kinds/{kind}/unmute')
  const kinds = e.kinds ?? []
  if (!kinds.length) return null
  const at = (kind: string, key: string) => [...EXTENSIONS, e.id, 'kinds', kind, key]
  const set = (kind: string, key: string, value: unknown) => page.update((c) => setIn(c, at(kind, key), value))
  const nudges = (e.nudges ?? []).filter((n) => n.state !== 'ended').slice(0, 5)
  return (
    <div className="flex flex-col gap-3">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Speaking up</TableHead>
            <TableHead>When</TableHead>
            <TableHead>Where</TableHead>
            <TableHead>On</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {kinds.map((k) => {
            const saved = getIn(page.value, [...EXTENSIONS, e.id, 'kinds', k.kind]) as Record<string, unknown> | undefined
            const deliver = (saved?.deliver ?? k.declared_deliver) as unknown
            const where = (saved?.where ?? k.declared_where) as unknown
            const how = [
              k.checked ? (k.triggers?.length ? `Checked ${k.triggers.join(', ')}` : 'Checked when the extension runs') : 'Sent by the extension',
              k.requested ? 'what you ask for comes when you asked, through quiet hours' : null,
              k.may_be_urgent ? 'may be urgent, and then spoken in quiet hours' : null,
              k.repeat ? `said again after ${Math.round(k.repeat / 3600)} hours while it holds` : null,
            ].filter(Boolean)
            const pick = (value: unknown, labels: Record<string, string>, key: 'deliver' | 'where', declared: unknown) => (
              <Select value={typeof value === 'string' ? value : PER_LEVEL} onValueChange={(v) => set(k.kind, key, v === declared ? null : v)}>
                <SelectTrigger size="sm" aria-label={`${key === 'deliver' ? 'When' : 'Where'} ${k.title} is said`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {typeof value !== 'string' && <SelectItem value={PER_LEVEL}>By level</SelectItem>}
                  {Object.entries(labels).map(([v, label]) => (
                    <SelectItem key={v} value={v}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )
            return (
              <TableRow key={k.kind}>
                <TableCell className="whitespace-normal">
                  <span className="flex flex-wrap items-center gap-1.5">
                    {k.title}
                    <InfoTip text={how.join('; ') + '.'} />
                    {k.muted_until != null && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 px-1.5 text-muted-foreground"
                        disabled={unmute.isPending}
                        onClick={() => unmute.mutate({ params: { path: { ext_id: e.id, kind: k.kind } } })}
                      >
                        <BellOff />
                        {k.muted_until === 0
                          ? 'Muted, switch back on'
                          : `Muted until ${new Date(k.muted_until * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`}
                      </Button>
                    )}
                  </span>
                </TableCell>
                <TableCell>{pick(deliver, DELIVER, 'deliver', k.declared_deliver)}</TableCell>
                <TableCell>{pick(where, WHERE, 'where', k.declared_where)}</TableCell>
                <TableCell>
                  <Switch checked={saved?.enabled !== false} aria-label={`Let him say ${k.title}`} onCheckedChange={(v) => set(k.kind, 'enabled', v ? null : false)} />
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      {nudges.length > 0 && (
        <div className="flex flex-col gap-2 text-sm">
          {nudges.map((n) => (
            <div key={n.id} className="flex flex-col">
              <span>{n.say}</span>
              <span className="text-xs text-muted-foreground">
                {n.title}: {standing(n)}
                {n.reason ? `. You said: ${n.reason}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function hasFields(page: SettingsState, path: string[]): boolean {
  try {
    return Object.keys(section(page.schema, path).properties ?? {}).length > 0
  } catch {
    return false
  }
}

function ExtensionCard({ e, page }: Props) {
  const [log, setLog] = useState(false)
  const restart = $api.useMutation('post', '/api/extensions/{ext_id}/restart')
  const path = [...EXTENSIONS, e.id]
  const enabled = getIn(page.value, [...path, 'enabled']) === true
  const may = reach(e)
  const settingsPath = [...path, 'settings']
  const values = (e.entities ?? []).flatMap((x) =>
    (x.values ?? []).map((v) => ({ key: `${x.key}:${v.entry ?? ''}`, name: v.entry ? x.name.replace('{entry}', v.entry) : x.name, value: v.value, unit: x.unit })),
  )
  const problem = e.problem ?? (e.state === 'failed' || e.state === 'restarting' ? e.detail : null)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <ExtensionIcon name={e.icon ?? 'puzzle'} />
          {e.name}
          <StateIcon state={STATE[e.state as keyof typeof STATE] ?? 'stopped'} detail={e.problem || e.detail || e.state} />
          {e.origin !== 'built-in' && (
            <StatusTip
              icon={ShieldAlert}
              className="text-muted-foreground"
              label="Not built in. It runs as you, not in a sandbox; switch on only ones you trust."
            />
          )}
          <span className="ml-auto flex items-center gap-1">
            <IconButton label="Log" onClick={() => setLog(true)}>
              <ScrollText />
            </IconButton>
            {e.enabled && (
              <IconButton label="Restart" disabled={restart.isPending} onClick={() => restart.mutate({ params: { path: { ext_id: e.id } } })}>
                <RotateCw />
              </IconButton>
            )}
            <Switch
              checked={enabled}
              aria-label={enabled ? `Switch ${e.name} off` : `Switch ${e.name} on`}
              onCheckedChange={(v) => page.update((c) => setIn(c, [...path, 'enabled'], v))}
            />
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {e.description && <p className="text-sm text-muted-foreground">{e.description}</p>}
        {problem && (
          <div role="alert" className="flex items-start gap-2 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{problem}</span>
          </div>
        )}
        {e.status && e.state === 'running' && <p className="text-sm">{e.status}</p>}
        {!problem && (e.exits ?? []).length > 0 && (
          <div className="flex items-start gap-2 text-sm text-muted-foreground">
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
            <span>
              Stopped {(e.exits ?? []).length === 1 ? 'once' : `${(e.exits ?? []).length} times`} lately, last at{' '}
              {new Date((e.exits ?? [])[(e.exits ?? []).length - 1].at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}:{' '}
              {(e.exits ?? [])[(e.exits ?? []).length - 1].why}
            </span>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5 text-sm">
          <span className="mr-1 text-muted-foreground">May reach</span>
          {e.origin === 'mcp' ? (
            <Badge variant="outline">Not declared</Badge>
          ) : may.length ? (
            may.map((m) => (
              <Badge key={m} variant="outline">
                {m}
              </Badge>
            ))
          ) : (
            <Badge variant="outline">Nothing outside itself</Badge>
          )}
          <span className="ml-auto text-xs text-muted-foreground">{ORIGIN[e.origin] ?? e.origin}</span>
        </div>
        {hasFields(page, settingsPath) && <SectionForm page={page} path={settingsPath} />}
        {e.origin === 'mcp' && page.advanced && <SectionForm page={page} path={[...path, 'mcp']} />}
        <Tools e={e} page={page} />
        <SpeakingUp e={e} page={page} />
        {values.length > 0 && (
          <div className="flex flex-col gap-1 text-sm">
            {values.map((v) => (
              <div key={v.key} className="flex gap-2">
                <span className="text-muted-foreground">{v.name}</span>
                <span>
                  {String(v.value ?? 'Unknown')}
                  {v.unit ? ` ${v.unit}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
        {e.state === 'running' && (Boolean(e.memory_mb) || Boolean(e.prompt_chars)) && (
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            {Boolean(e.memory_mb) && (
              <span className="flex items-center gap-1.5">
                <MemoryStick className="size-3.5" />
                {Math.round(e.memory_mb ?? 0)} of {e.memory_limit_mb} MB
              </span>
            )}
            {Boolean(e.prompt_chars) && (
              <span className="flex items-center gap-1.5">
                <MessageSquareText className="size-3.5" />
                {(e.prompt_chars ?? 0).toLocaleString()} characters in every prompt
              </span>
            )}
          </div>
        )}
      </CardContent>
      <LogDialog id={e.id} open={log} onOpenChange={setLog} />
    </Card>
  )
}

/** An MCP server added as a tools-only extension: its id, how it starts (a command or a URL) and the
 * variables from data/.env it gets. Saved with the page. */
function AddMcp({ page, taken, open, onOpenChange }: { page: SettingsState; taken: string[]; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [id, setId] = useState('')
  const [start, setStart] = useState('')
  const [env, setEnv] = useState('')
  const url = /^https?:\/\//.test(start.trim())
  const command = start.match(/"[^"]*"|'[^']*'|\S+/g)?.map((a) => a.replace(/^["']|["']$/g, '')) ?? []
  const problem = !ID.test(id) ? 'Lowercase letters, digits and underscores' : taken.includes(id) ? 'Already in the list' : !start.trim() ? 'How it starts' : null
  const add = () => {
    const mcp = { ...(url ? { url: start.trim() } : { command }), env: env.split(/[\s,]+/).filter(Boolean) }
    page.update((c) => setIn(c, [...EXTENSIONS, id], { enabled: true, mcp }))
    onOpenChange(false)
    setId('')
    setStart('')
    setEnv('')
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an MCP server</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-id">Id</Label>
            <Input id="mcp-id" value={id} onChange={(ev) => setId(ev.target.value)} placeholder="hevy_mcp" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-start" className="flex items-center gap-1.5">
              Command or URL
              <InfoTip text="How it starts: a command that speaks MCP over stdio (npx -y hevy-mcp), or the URL of one that speaks it over HTTP." />
            </Label>
            <Input id="mcp-start" value={start} onChange={(ev) => setStart(ev.target.value)} placeholder="npx -y hevy-mcp" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-env" className="flex items-center gap-1.5">
              Variables
              <InfoTip text="Names in data/.env it gets, and no others (HEVY_API_KEY)." />
            </Label>
            <Input id="mcp-env" value={env} onChange={(ev) => setEnv(ev.target.value)} placeholder="HEVY_API_KEY" />
          </div>
        </div>
        <DialogFooter>
          {problem && id && <span className="mr-auto text-sm text-muted-foreground">{problem}</span>}
          <Button disabled={problem !== null} onClick={add}>
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function ExtensionsPage() {
  const s = useSettings([SPEAK_UP, EXTENSIONS])
  const client = useQueryClient()
  const list = $api.useQuery('get', '/api/extensions', undefined, { refetchInterval: 3000 })
  const [adding, setAdding] = useState(false)
  const ids = useMemo(() => (list.data ?? []).map((e) => e.id).join(','), [list.data])
  // A newly found extension brings its settings into the config schema: read it again.
  useEffect(() => {
    if (!s.page || !ids) return
    const known = Object.keys(section(s.page.schema, EXTENSIONS).properties ?? {})
    if (ids.split(',').some((id) => !known.includes(id))) void client.invalidateQueries({ queryKey: schemaKey })
  }, [ids, s.page, client])
  if (s.loading || !list.data) return <PageSkeleton title="Extensions" />
  const { page } = s
  const drafted = Object.keys((page.value.extensions ?? {}) as Record<string, unknown>).filter((id) => !list.data.some((e) => e.id === id))
  return (
    <SettingsPage
      title="Extensions"
      page={page}
      actions={
        <IconButton label="Add an MCP server" onClick={() => setAdding(true)}>
          <Plus />
        </IconButton>
      }
    >
      <SectionForm page={page} path={SPEAK_UP} title="Speaking up" />
      {list.data.length === 0 && drafted.length === 0 && (
        <p className="text-sm text-muted-foreground">None yet. `winston ext new NAME` makes one in data/extensions/.</p>
      )}
      {list.data.map((e) => (
        <ExtensionCard key={e.id} e={e} page={page} />
      ))}
      {drafted.map((id) => (
        <Card key={id}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ExtensionIcon name="plug" />
              {id}
              <span className="text-sm font-normal text-muted-foreground">Starts once saved</span>
            </CardTitle>
          </CardHeader>
        </Card>
      ))}
      <AddMcp page={page} taken={[...list.data.map((e) => e.id), ...drafted]} open={adding} onOpenChange={setAdding} />
    </SettingsPage>
  )
}
