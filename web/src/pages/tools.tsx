import { useMemo, useState } from 'react'
import { CircleCheck, CircleX, Trash2 } from 'lucide-react'
import { $api, type ClaudeAuth, type SensorPair } from '@/api/client'
import type { Choice } from '@/components/combobox'
import { IconButton } from '@/components/icon-button'
import { ItemDialog } from '@/components/item-dialog'
import { PageSkeleton } from '@/components/page'
import { SecretField } from '@/components/secret-field'
import { SectionForm, type ExtraRow } from '@/components/section-form'
import { ListPage, type Column } from '@/components/templates/list-page'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfirm } from '@/hooks/use-confirm'
import { useOptions } from '@/hooks/use-options'
import { useSettings } from '@/hooks/use-settings'
import { prune, section } from '@/lib/schema'

const SENSORS = ['tools', 'sensors']
const WEB = ['tools', 'web_search']
const CLAUDE = ['tools', 'ask_claude']
const MEMORY = ['memory']
const SENSOR_DOMAINS = { domain: 'sensor,binary_sensor' }

type Row = SensorPair & { label: string }
type Editing = { label: string; original?: string; value: SensorPair }

/** A picked value sensor fills in what can be known from it: the label from its name in Home
 * Assistant, the state sensor from its sibling (battery_level -> battery_state). */
function autofill(e: Editing, next: SensorPair, known: Map<string, Choice>): Editing {
  if (!next.level || next.level === e.value.level) return { ...e, value: next }
  const label = e.label.trim() ? e.label : (known.get(next.level)?.label ?? e.label)
  const sibling = next.level.replace(/_level$/, '_state')
  const state = next.state || (sibling !== next.level && known.has(sibling) ? sibling : undefined)
  return { ...e, label, value: { ...next, state } }
}

function Sensor({ id, known }: { id?: string | null; known: Map<string, Choice> }) {
  if (!id) return null
  const c = known.get(id)
  return (
    <div className="flex flex-col">
      <span>{c?.hint ?? id}</span>
      <span className="text-xs break-all text-muted-foreground">{id}</span>
    </div>
  )
}

const CLAUDE_NAMES = ['CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY']

/** A pasted Claude key goes under the variable its prefix names; anything else is refused on save. */
const claudeName = (value: string) =>
  value.startsWith('sk-ant-oat') ? 'CLAUDE_CODE_OAUTH_TOKEN' : value.startsWith('sk-ant-api') ? 'ANTHROPIC_API_KEY' : undefined

const SOURCES: Record<ClaudeAuth['source'], string> = {
  api_key: 'ANTHROPIC_API_KEY is set',
  oauth_token: 'CLAUDE_CODE_OAUTH_TOKEN is set',
  login: 'Found a Claude Code login in ~/.claude',
  none: 'No key, token or Claude Code login found',
}

/** What the Claude CLI will sign in with, read-only: its label and an icon, the source on hover. */
function SignsInWith({ id }: { id: string }) {
  const auth = $api.useQuery('get', '/api/tools/ask_claude/auth')
  if (!auth.data) return <span id={id} className="text-sm text-muted-foreground">Checking</span>
  const found = auth.data.source !== 'none'
  const Icon = found ? CircleCheck : CircleX
  return (
    <div id={id} className="flex items-center gap-2 py-1 text-sm">
      <span>{auth.data.label}</span>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Icon className={found ? 'size-4 text-success' : 'size-4 text-destructive'} aria-label={SOURCES[auth.data.source]} />
          </span>
        </TooltipTrigger>
        <TooltipContent>{SOURCES[auth.data.source]}</TooltipContent>
      </Tooltip>
    </div>
  )
}

const CLAUDE_EXTRAS: ExtraRow[] = [
  {
    key: 'signs_in_with',
    title: 'Signs in with',
    description:
      'What the Claude CLI uses, first found wins: an API key (ANTHROPIC_API_KEY), a token from claude setup-token ' +
      '(CLAUDE_CODE_OAUTH_TOKEN), then the Claude Code login on this machine.',
    after: 'enabled',
    render: (id) => <SignsInWith id={id} />,
  },
  {
    key: 'key',
    title: 'Key',
    description:
      "A token from claude setup-token (sk-ant-oat...) uses your Claude plan's credit; an API key (sk-ant-api...) " +
      'is billed per question. Kept in data/.env on this machine and never shown again.',
    after: 'signs_in_with',
    render: (id) => (
      <SecretField
        id={id}
        names={CLAUDE_NAMES}
        resolve={claudeName}
        label="Key"
        placeholder="Paste a token from claude setup-token, or an API key"
        optional
      />
    ),
  },
]

export default function ToolsPage() {
  const s = useSettings([WEB, CLAUDE, MEMORY])
  const ask = useConfirm()
  const [editing, setEditing] = useState<Editing | null>(null)
  const sensorOptions = useOptions('ha_entities', SENSOR_DOMAINS)
  const known = new Map(sensorOptions.choices.map((c) => [c.value, c]))
  const columns: Column<Row>[] = [
    { key: 'label', title: 'Label', className: 'whitespace-normal', render: (r) => <span className="font-medium">{r.label}</span> },
    { key: 'level', title: 'Value', className: 'whitespace-normal', render: (r) => <Sensor id={r.level} known={known} /> },
    { key: 'state', title: 'State', className: 'whitespace-normal', render: (r) => <Sensor id={r.state} known={known} /> },
  ]
  const sensorSchema = useMemo(
    () => (s.page ? prune(section(s.page.schema, [...SENSORS, 'item']), s.page.schema, { advanced: true }) : null),
    [s.page],
  )
  if (s.loading || !sensorSchema) return <PageSkeleton title="Tools" />
  const { page } = s
  const sensors = page.value.tools?.sensors ?? {}
  const setSensors = (next: Record<string, SensorPair>) =>
    page.update((c) => ({ ...c, tools: { ...c.tools, sensors: next } }))
  const rows: Row[] = Object.entries(sensors).map(([label, pair]) => ({ label, ...pair }))

  const commit = () => {
    if (!editing) return
    const next = { ...sensors }
    if (editing.original && editing.original !== editing.label) delete next[editing.original]
    next[editing.label.trim()] = editing.value
    setSensors(next)
    setEditing(null)
  }
  const remove = async (label: string) => {
    const choice = await ask({ title: `Remove ${label}?`, choices: [{ label: 'Remove', value: 'yes', destructive: true }] })
    if (!choice) return
    const next = { ...sensors }
    delete next[label]
    setSensors(next)
  }

  return (
    <ListPage
      title="Tools"
      page={page}
      advancedToggle
      addLabel="Add sensor"
      onAdd={() => setEditing({ label: '', value: { level: '' } })}
      columns={columns}
      rows={rows}
      rowKey={(r) => r.label}
      onRowClick={(r) => setEditing({ label: r.label, original: r.label, value: { level: r.level, state: r.state } })}
      actions={(r) => (
        <IconButton label="Remove" onClick={() => remove(r.label)}>
          <Trash2 />
        </IconButton>
      )}
    >
      <SectionForm page={page} path={WEB} title="Web search" />
      <SectionForm page={page} path={CLAUDE} title="Ask Claude" icon="anthropic" extras={CLAUDE_EXTRAS} />
      <SectionForm page={page} path={MEMORY} title="Memory" />
      <ItemDialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing?.original ? editing.original : 'New sensor'}
        keyLabel="Label"
        keyValue={editing?.label ?? ''}
        onKeyChange={(label) => setEditing((e) => e && { ...e, label })}
        existing={Object.keys(sensors)}
        original={editing?.original}
        schema={sensorSchema}
        value={editing?.value}
        onChange={(value) => setEditing((e) => e && autofill(e, value as SensorPair, known))}
        onSubmit={commit}
      />
    </ListPage>
  )
}
