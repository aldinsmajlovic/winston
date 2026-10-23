import { createContext, useCallback, useContext, type Dispatch, type SetStateAction } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'react-router'
import { toast } from 'sonner'
import {
  $api,
  errorText,
  fetchClient,
  type Config,
  type EngineStatus,
  type ExtensionView,
  type PersonaRename,
  type SecretStatus,
} from '@/api/client'
import { clearFieldErrors, hiddenAt, showFieldErrors } from '@/api/field-errors'
import { configKey, patchConfig, useConfig, useSchema, type ConfigChange, type PatchOutcome } from '@/api/config'
import { engineLabel, isRunning, partName, restartCost, statusKey, useEngineActions } from '@/api/status'
import { useConfirm } from '@/hooks/use-confirm'
import { useDraft } from '@/hooks/use-draft'
import { DOWNLOAD_COST, mustDownload, optionsParams } from '@/hooks/use-options'
import { haNames, planParts, refreshHomeAssistant, toastSaved, type ExposurePlan, type HAApply } from '@/hooks/use-home-assistant'
import { secretRestarts, secretsKey, useSecretStatus, useSecretsDraft } from '@/hooks/use-secrets'
import { humanize, joinNames } from '@/lib/format'
import { presetSwitches } from '@/lib/presets'
import { deepEqual, deref, getIn, isRecord, SECRET_SUFFIX, section, type JsonSchema, type Path } from '@/lib/schema'

type Edits = { changes: ConfigChange[]; version: string | undefined }

const personaKey = $api.queryOptions('get', '/api/persona').queryKey

/** The unsaved config behind every settings page, as changed paths over the saved config, and the
 * version they started from. Pasted keys wait in the secrets draft (use-secrets), field errors from
 * a refused save in field-errors. */
export type DraftStore = {
  /** Edits that still differ from the saved config. */
  edits: ConfigChange[]
  version: string | undefined
  setEdits: Dispatch<SetStateAction<Edits>>
  saving: boolean
  setSaving: (saving: boolean) => void
}

export const DraftStoreContext = createContext<DraftStore | null>(null)

function useDraftStore(): DraftStore {
  const store = useContext(DraftStoreContext)
  if (!store) throw new Error('useDraftStore outside DraftProvider')
  return store
}

// Paths over the config. A null value removes the key, as PATCH does.

const same = (a: unknown, b: unknown) => (a == null && b == null) || deepEqual(a, b)

/** setIn from lib/schema, except that null removes the key. */
function setPath(value: unknown, path: Path, next: unknown): unknown {
  if (path.length === 0) return next
  const [head, ...rest] = path
  if (Array.isArray(value) && typeof head === 'number') {
    const copy = [...value]
    copy[head] = setPath(copy[head], rest, next)
    return copy
  }
  const base: Record<string, unknown> = isRecord(value) ? { ...value } : {}
  const key = String(head)
  if (rest.length === 0 && next == null) delete base[key]
  else base[key] = setPath(base[key], rest, next)
  return base
}

/** The leaf paths where `b` differs from `a`; lists count as one value. */
export function diffConfig(a: unknown, b: unknown, path: Path = []): ConfigChange[] {
  if (isRecord(a) && isRecord(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    return [...keys].flatMap((k) => diffConfig(a[k], b[k], [...path, k]))
  }
  if (path.length === 0 || same(a, b)) return []
  return [{ path, value: b === undefined ? null : b }]
}

export function applyChanges<T>(value: T, changes: ConfigChange[]): T {
  return changes.reduce<unknown>((v, c) => setPath(v, c.path, c.value), value) as T
}

export function effectiveChanges(saved: unknown, changes: ConfigChange[]): ConfigChange[] {
  return changes.filter((c) => !same(getIn(saved, c.path), c.value))
}

/** Whether a path lies under a section; '*' in the section matches any one key (an extension's id). */
const under = (path: Path, section: string[]) =>
  path.length >= section.length && section.every((step, i) => step === '*' || path[i] === step)

// Which page edits which part of the config (pages/*.tsx), for the sidebar's dots and the confirms.
// `views` are another page's editor shown filtered there (an extension's own actions and messages).

export type SettingsPageInfo = { to: string; label: string; sections: string[][]; views?: string[][] }

export const settingsPages: SettingsPageInfo[] = [
  { to: '/status', label: 'Status', sections: [['engines'], ['logging'], ['control'], ['version']] },
  { to: '/assistant', label: 'Assistant', sections: [['assistant'], ['region'], ['memory'], ['logging', 'turns']] },
  { to: '/brain', label: 'Brain', sections: [['llm'], ['engines', 'llm']] },
  { to: '/voice', label: 'Voice', sections: [['tts'], ['stt'], ['engines', 'tts'], ['engines', 'stt']] },
  { to: '/ways-to-talk', label: 'Ways to talk', sections: [['channels'], ['vad']] },
  { to: '/home-assistant', label: 'Home Assistant', sections: [['providers']] },
  {
    to: '/extensions',
    label: 'Extensions',
    sections: [['extensions']],
    views: [['extensions', '*', 'tools'], ['extensions', '*', 'kinds']],
  },
  { to: '/actions', label: 'Actions', sections: [['extensions', '*', 'tools']] },
  { to: '/speaking-up', label: 'Speaking up', sections: [['speak_up'], ['announce'], ['extensions', '*', 'kinds']] },
]

/** How far into a path a page's sections reach: its longest section that holds the path, 0 for none. */
const depthIn = (page: SettingsPageInfo, path: Path) => Math.max(0, ...page.sections.filter((s) => under(path, s)).map((s) => s.length))

/** The pages that show a path: first its owner, the page with the longest matching section (engines.llm.args
 * is Brain's, not Status's; ties keep every tied page), then the pages that show it in a view. */
export function pagesOf(path: Path): SettingsPageInfo[] {
  const longest = Math.max(0, ...settingsPages.map((p) => depthIn(p, path)))
  const owners = longest ? settingsPages.filter((p) => depthIn(p, path) === longest) : []
  const viewers = settingsPages.filter((p) => !owners.includes(p) && p.views?.some((v) => under(path, v)))
  return [...owners, ...viewers]
}

const pageAt = (pathname: string) => settingsPages.find((p) => pathname.startsWith(p.to))

/** A path's names from the schema titles, one per key it passes, with where it last crosses a keyed map's
 * key (`keyAt` among the names, `keyStep` in the path). */
function labelSteps(root: JsonSchema, path: Path, from: JsonSchema = root) {
  let current: JsonSchema | undefined = deref(from, root)
  const parts: string[] = []
  let keyAt = -1
  let keyStep = -1
  for (const [i, step] of path.entries()) {
    if (!current) {
      parts.push(String(step))
      continue
    }
    if (typeof step === 'number') {
      current = current.items ? deref(current.items, root) : undefined
      continue
    }
    const property: JsonSchema | undefined = current.properties?.[step]
    if (property) {
      current = deref(property, root)
      // A section's own title is its model's class name ("AnnounceConfig"); its key reads better.
      parts.push(property.title ?? (property.$ref ? humanize(step) : (current.title ?? humanize(step))))
    } else if (isRecord(current.additionalProperties)) {
      current = deref(current.additionalProperties as JsonSchema, root)
      keyAt = parts.length
      keyStep = i
      parts.push(step)
    } else {
      current = undefined
      parts.push(humanize(step))
    }
  }
  return { parts, keyAt, keyStep }
}

/** A field's name for a person, from the schema titles: "Voice", "Contacts: Mom", "llm: Args". A section's
 * switch is titled as a sentence naming it ("Talk to him on Discord"). `from` is the section the path
 * starts in (one extension's), else the config. */
export function fieldLabel(root: JsonSchema, path: Path, from: JsonSchema = root): string {
  const { parts, keyAt } = labelSteps(root, path, from)
  const last = parts.length - 1
  if (last < 0) return 'Config'
  if (keyAt === last && last > 0) return `${parts[last - 1]}: ${parts[last]}`
  if (keyAt >= 0 && keyAt < last) return `${parts[keyAt]}: ${parts[last]}`
  return parts[last]
}

/** A field's name on the page that owns it: a key that page's own section names (engines.llm on Brain)
 * is the page, not part of the name, so "Startup time limit" and not "llm: Startup time limit". */
function pageFieldLabel(root: JsonSchema, path: Path, owner?: SettingsPageInfo): string {
  const { parts, keyAt, keyStep } = labelSteps(root, path)
  const inPage = keyStep >= 0 && keyStep < (owner ? depthIn(owner, path) : 0)
  return inPage && keyAt < parts.length - 1 ? parts[parts.length - 1] : fieldLabel(root, path)
}

/** The config paths that name a key, so it counts on their page; none for a name nothing uses yet. */
const secretPaths = (name: string, byName: Map<string, SecretStatus>): Path[] => byName.get(name)?.used_by ?? []

/** A pasted key by its field's label ("Bot token"), else its variable name. */
export function keyLabel(root: JsonSchema, path: Path, name: string): string {
  let current: JsonSchema | undefined = deref(root, root)
  for (const step of path) {
    const next: JsonSchema | undefined =
      typeof step === 'number'
        ? current?.items
        : (current?.properties?.[step] ??
          (isRecord(current?.additionalProperties) ? (current.additionalProperties as JsonSchema) : undefined))
    current = next ? deref(next, root) : undefined
  }
  const label = isRecord(current?.secret) ? current.secret.label : undefined
  return typeof label === 'string' ? label : name
}

/** What the confirms name edits by: the schema, the saved config and the draft, the keys' variables, and
 * the extensions found (their names and their actions' and messages' titles). */
type Names = { root: JsonSchema; saved: Config; value: Config; byName: Map<string, SecretStatus>; extensions: ExtensionView[] }

/** The draft field whose value is a variable's name: where a key is pasted before the config that names it
 * is saved (a new MCP server's token). */
function namedAt(value: unknown, name: string, path: Path = []): Path | undefined {
  if (value === name) return path
  if (!isRecord(value)) return undefined
  for (const [k, v] of Object.entries(value)) {
    const found = namedAt(v, name, [...path, k])
    if (found) return found
  }
  return undefined
}

/** An edit under an extension as its pages show it: an action or a message by its title, "Play music
 * (Spotify)"; the extension itself, "Spotify (removed)"; else "Spotify: Speaker name". */
function extensionLabel(names: Names, path: Path, removed: boolean): string {
  const id = String(path[1])
  const view = names.extensions.find((e) => e.id === id)
  // One not found yet (an MCP server added in this draft) by the name it is given, else its id.
  const title = getIn(names.value, ['extensions', id, 'mcp', 'title'])
  const name = view?.name ?? (typeof title === 'string' && title ? title : id)
  const [field, key] = [path[2], path[3] === undefined ? undefined : String(path[3])]
  if (field === undefined) return removed ? `${name} (removed)` : name
  if (field === 'tools' && key) return `${view?.tools?.find((t) => t.name === key)?.title ?? humanize(key)} (${name})`
  if (field === 'kinds' && key) return `${view?.kinds?.find((k) => k.kind === key)?.title ?? humanize(key)} (${name})`
  return `${name}: ${fieldLabel(names.root, path.slice(2), section(names.root, ['extensions', id]))}`
}

/** "Voice: Voice, Speed" per page, for the confirms. The entries of a list keyed by name read as one label,
 * "Devices he can use: Desk lamp, PC (removed)". */
function summary(names: Names, edits: ConfigChange[], keys: string[], switches: { line: string; paths: string[][] }[] = []): string[] {
  const { root, byName } = names
  // Per page, each label with the entries it names.
  const groups = new Map<string, Map<string, Set<string>>>()
  const add = (page: string, label: string, entry?: string) => {
    const labels = groups.get(page) ?? new Map<string, Set<string>>()
    const entries = labels.get(label) ?? new Set<string>()
    if (entry) entries.add(entry)
    groups.set(page, labels.set(label, entries))
  }
  const nameOf = (entry: unknown) => (isRecord(entry) && typeof entry.name === 'string' && entry.name ? entry.name : undefined)
  const switched = (path: Path) => switches.some((w) => w.paths.some((p) => p.every((k, i) => path[i] === k)))
  for (const c of edits) {
    if (switched(c.path)) continue
    const owner = pagesOf(c.path)[0]
    const page = owner?.label ?? 'Other'
    if (c.path[0] === 'extensions' && c.path.length > 1) {
      add(page, extensionLabel(names, c.path, c.value === null))
      continue
    }
    const { parts, keyAt, keyStep } = labelSteps(root, c.path)
    // A key the page's own section names (engines.llm on Brain) is not an entry: the field inside it is.
    if (keyStep >= 0 && keyStep < (owner ? depthIn(owner, c.path) : 0)) {
      add(page, pageFieldLabel(root, c.path, owner))
    } else if (keyAt > 0) {
      // An entry by its name in the draft, else as saved (one being removed), else its key.
      const at = c.path.slice(0, keyStep + 1)
      const entry = getIn(names.value, at)
      const name = nameOf(entry) ?? nameOf(getIn(names.saved, at)) ?? String(c.path[keyStep])
      add(page, parts[keyAt - 1], entry == null ? `${name} (removed)` : name)
    } else {
      add(page, fieldLabel(root, c.path))
    }
  }
  for (const name of keys) {
    // A key the saved config does not name yet (a new MCP server's token) is found by the draft field naming it.
    const path = secretPaths(name, byName)[0] ?? namedAt(names.value, name)
    add((path && pagesOf(path)[0]?.label) || 'Keys', path ? keyLabel(root, path, name) : name)
  }
  const line = (labels: Map<string, Set<string>>) => {
    const named = [...labels].map(([label, entries]) => (entries.size ? `${label}: ${[...entries].join(', ')}` : label))
    // Labels that list entries are kept apart by semicolons, so an entry never reads as a field.
    return named.join([...labels.values()].some((e) => e.size) ? '; ' : ', ')
  }
  return [...switches.map((w) => w.line), ...[...groups].map(([page, labels]) => `${page}: ${line(labels)}`)]
}

/** The pages with unsaved edits or pasted keys, for the sidebar's dots. */
export function useDirtyPages(): Set<string> {
  const { edits } = useDraftStore()
  const secrets = useSecretsDraft()
  const { byName } = useSecretStatus()
  const paths = [...edits.map((c) => c.path), ...Object.keys(secrets.pending).flatMap((n) => secretPaths(n, byName))]
  return new Set(paths.flatMap((p) => pagesOf(p).map((page) => page.to)))
}

export type SettingsState = {
  schema: JsonSchema
  value: Config
  dirty: boolean
  /** How many fields and pasted keys are unsaved. */
  changes: number
  saving: boolean
  update: (fn: (current: Config) => Config) => void
  reset: () => void
  /** Whether it saved: false when asked and cancelled, refused or failed. */
  save: () => Promise<boolean>
}

type Loading = { loading: true; page: null }
type Ready = { loading: false; page: SettingsState }

/** Whether this save's brain restart downloads the new model first. Asks winstond, never the Hub. */
async function needsDownload(changes: ConfigChange[], config: Config, restartable: EngineStatus[]): Promise<boolean> {
  if (!restartable.some((e) => e.name === 'llm') || !changes.some((c) => c.path.join('.') === 'llm.model')) return false
  const { data } = await fetchClient.GET('/api/options/{name}', optionsParams('llm_models', config))
  return mustDownload(data, config.llm.model)
}

const channelIn = (c: Config, name: string) =>
  (c.channels as Record<string, { enabled?: boolean; devices?: object } | undefined> | undefined)?.[name]
const boxes = (c: Config) => Object.keys(channelIn(c, 'satellite')?.devices ?? {}).length

/** Running parts this draft switches off or removes (an engine, a channel, the room boxes' last box),
 * against the saved config: saving stops them. */
function stoppedBy(saved: Config, draft: Config, parts: EngineStatus[]): EngineStatus[] {
  const off = (c: Config, e: EngineStatus) => {
    if (e.kind === 'engine') return !c.engines?.[e.name] || c.engines[e.name].enabled === false
    return channelIn(c, e.name)?.enabled === false || (e.name === 'satellite' && boxes(c) === 0)
  }
  return parts.filter((e) => isRunning(e) && off(draft, e) && !off(saved, e))
}

/** Channels this draft switches on that are not running (the room boxes once they have a box): saving
 * starts them. */
function startedBy(saved: Config, draft: Config, parts: EngineStatus[]): string[] {
  return ['voice', 'discord', 'satellite'].filter(
    (name) =>
      channelIn(saved, name)?.enabled !== true &&
      channelIn(draft, name)?.enabled === true &&
      (name !== 'satellite' || boxes(draft) > 0) &&
      !parts.some((e) => e.name === name && isRunning(e)),
  )
}

/** What the save would change in Home Assistant, from the dry run, as one confirm line; none when nothing.
 * `name` names the entities it stops exposing. */
function haPlanLine(plan: ExposurePlan | null | undefined, error: string | null | undefined, name: (id: string) => string): string | null {
  if (error) return `Home Assistant: could not preview what changes there (${error})`
  const parts = plan ? planParts(plan, name) : []
  return parts.length ? `Home Assistant: ${parts.join(', ')}` : null
}

/** A page's view of the shared draft plus the save flow: a dry-run patch of only the changed paths,
 * confirm what changes, starts, stops and restarts, save with the version the edits started from, write
 * pending keys, restart. */
export function useSettings(): Loading | Ready {
  const client = useQueryClient()
  const config = useConfig()
  const schema = useSchema()
  const actions = useEngineActions()
  const status = $api.useQuery('get', '/api/status')
  const presets = $api.useQuery('get', '/api/presets')
  const extensions = $api.useQuery('get', '/api/extensions')
  const secrets = useSecretsDraft()
  const { byName } = useSecretStatus()
  const ask = useConfirm()
  const { pathname } = useLocation()
  const { draft, setDraft } = useDraft()
  const store = useDraftStore()

  const update = useCallback((fn: (current: Config) => Config) => setDraft((d) => (d ? fn(d) : d)), [setDraft])

  const base = config.data
  if (!base || !schema.data) return { loading: true, page: null }
  const root = schema.data
  const value = draft ?? base
  const { edits } = store
  const keys = Object.keys(secrets.pending)
  const dirty = edits.length > 0 || keys.length > 0
  const current = pageAt(pathname)
  const onCurrent = (path: Path) => !!current && pagesOf(path).includes(current)
  const keyHere = (name: string) => secretPaths(name, byName).some(onCurrent)
  const elsewhere = edits.some((c) => !onCurrent(c.path)) || keys.some((n) => !keyHere(n))
  const names: Names = { root, saved: base, value, byName, extensions: extensions.data ?? [] }

  const discardAll = () => {
    store.setEdits({ changes: [], version: undefined })
    secrets.discard()
    clearFieldErrors()
  }

  /** Discard asks once when the edits reach past this page, offering this page alone. */
  const reset = async () => {
    if (!elsewhere) return discardAll()
    const here = !!current && (edits.some((c) => onCurrent(c.path)) || keys.some(keyHere))
    const choice = await ask({
      title: 'Discard unsaved changes?',
      lines: summary(names, edits, keys),
      choices: [
        ...(here && current ? [{ label: `Only ${current.label}`, value: 'page' }] : []),
        { label: 'Discard all', value: 'all', destructive: true },
      ],
    })
    if (choice === 'all') discardAll()
    if (choice !== 'page') return
    store.setEdits((e) => ({ ...e, changes: e.changes.filter((c) => !onCurrent(c.path)) }))
    secrets.discard(keys.filter(keyHere))
    clearFieldErrors()
  }

  /** A save the server refused: a conflict rebases the draft on the new config, a 422 marks fields. */
  const refused = async (outcome: Exclude<PatchOutcome, { ok: true }>) => {
    if ('conflict' in outcome) {
      const { conflict } = outcome
      // A 409 without a version is not a conflict of fields: config.yaml on disk does not load.
      if (!conflict?.version || !Array.isArray(conflict.paths)) {
        toast.error(errorText(conflict))
        return
      }
      await client.refetchQueries({ queryKey: configKey })
      store.setEdits((e) => ({ ...e, version: conflict.version }))
      const what = conflict.paths.length ? conflict.paths.map((p) => fieldLabel(root, p)).join(', ') : 'the config'
      toast.warning(`Changed elsewhere meanwhile: ${what}. Your edits are kept on top.`, {
        description: 'Check them and save again.',
      })
      return
    }
    const errors = outcome.status === 422 ? showFieldErrors(outcome.error, root) : []
    if (errors.length) {
      // A hidden field is on no page: its section and its own title, and where it is set. A key by its label.
      const line = (e: (typeof errors)[number]) => {
        const page = `${pagesOf(e.loc)[0]?.label ?? 'Config'}, `
        const last = String(e.loc[e.loc.length - 1])
        if (last.endsWith(SECRET_SUFFIX)) {
          const named = [...e.loc.slice(0, -1), last.slice(0, -SECRET_SUFFIX.length)]
          return `${page}${keyLabel(root, named, last)}: ${e.msg}`
        }
        return hiddenAt(root, e.loc)
          ? `${labelSteps(root, e.loc).parts.slice(-2).join(', ')}: ${e.msg} (in config.yaml)`
          : `${page}${pageFieldLabel(root, e.loc, pagesOf(e.loc)[0])}: ${e.msg}`
      }
      const lines = errors.map((e) => (e.loc.length ? line(e) : e.msg))
      toast.error('Not saved', { description: lines.join('; ') })
      return
    }
    toast.error(errorText(outcome.error))
  }

  const runSave = async () => {
    const changes = edits
    const version = store.version
    clearFieldErrors()
    let restarts: string[] = []
    let rename: PersonaRename | null | undefined
    let haLine: string | null = null
    if (changes.length) {
      const dry = await patchConfig(changes, { version, dryRun: true })
      if (!dry.ok) {
        await refused(dry)
        return false
      }
      restarts = dry.result.restarts
      rename = dry.result.persona_rename
      const ha = dry.result as { ha_plan?: ExposurePlan | null; ha_plan_error?: string | null }
      haLine = haPlanLine(ha.ha_plan, ha.ha_plan_error, haNames(client))
    }
    restarts = [...new Set([...restarts, ...secretRestarts(keys, byName)])]

    const engines = status.data?.engines ?? []
    // What the draft switches off or removes stops when saved (hearing switched back to Parakeet, Discord off).
    const stopping = stoppedBy(base, value, engines)
    const restarting = engines.filter((e) => restarts.includes(e.name) && isRunning(e) && !stopping.includes(e))
    // An engine the config gains, or a channel switched on, starts on save (winstond starts what it did not run before).
    const starting = [
      ...[...new Set(changes.filter((c) => c.path[0] === 'engines').map((c) => String(c.path[1])))].filter(
        (n) => value.engines?.[n] && value.engines[n].enabled !== false && !engines.some((e) => e.name === n),
      ),
      ...startedBy(base, value, engines),
    ]
    const later = restarts.some((name) => !engines.some((e) => e.name === name))
    const download = await needsDownload(changes, value, restarting)
    const costs = [
      ...stopping.map((e) => `${partName(e.name)}: stops now`),
      ...restarting.map((e) => `${partName(e.name)}: restarts, ${e.name === 'llm' && download ? DOWNLOAD_COST : restartCost(e)}`),
      ...starting.map((n) => `${partName(n)}: starts when saved`),
      ...(haLine ? [haLine] : []),
      ...(later ? ['Some changes take effect when Winston restarts'] : []),
    ]

    // A rename whose old name his persona says: the same confirm offers to change it there too.
    let renamePersona = false
    const check = rename
      ? {
          label: `His personality says ${rename.old} in ${rename.places} ${rename.places === 1 ? 'place' : 'places'}. Change ${rename.places === 1 ? 'it' : 'them'} to ${rename.new}?`,
          // A heading's marks ("## Voice") are the file's, not his words.
          detail: rename.lines.map((line) => {
            const text = line.replace(/^[#\s]+/, '')
            return text.length > 72 ? `${text.slice(0, 71)}…` : text
          }),
          checked: true,
          answer: (on: boolean) => { renamePersona = on },
        }
      : undefined
    // "Save and start the room boxes, restart the brain and stop Discord?"
    const verbs = [
      starting.length ? `start ${joinNames(starting.map(engineLabel))}` : null,
      restarting.length ? `restart ${joinNames(restarting.map(engineLabel))}` : null,
      stopping.length ? `stop ${joinNames(stopping.map(engineLabel))}` : null,
    ].filter((v) => v !== null)
    let restart = false
    if (costs.length || elsewhere || check) {
      const choice = await ask({
        // A rename leads (its restarts are in the lines and the choices), as Save and restart would bury it.
        title: rename ? `Rename him to ${rename.new}?` : verbs.length ? `Save and ${joinNames(verbs)}?` : 'Save these changes?',
        lines: [...summary(names, changes, keys, presetSwitches(base, value, presets.data)), ...costs],
        check,
        choices: restarting.length
          ? [
              { label: 'Save only', value: 'save' },
              { label: 'Save and restart', value: 'restart' },
            ]
          : [{ label: stopping.length ? 'Save and stop' : 'Save', value: 'save' }],
      })
      if (!choice) return false
      restart = choice === 'restart'
    }

    // The config first, so a variable name a key is pasted under is saved before the key.
    let haApply: HAApply | null | undefined
    if (changes.length) {
      const saved = await patchConfig(changes, { version, client, renamePersona })
      if (!saved.ok) {
        await refused(saved)
        return false
      }
      haApply = saved.result.ha_apply
      if (saved.result.persona_rename?.renamed) void client.invalidateQueries({ queryKey: personaKey })
    }
    const written = await secrets.save()
    for (const line of written.errors) toast.error(`Key not saved: ${line}`)
    // The saved config replaces the draft before it is cleared, so the page never shows the old one.
    await client.refetchQueries({ queryKey: configKey })
    store.setEdits({ changes: [], version: undefined })
    void client.invalidateQueries({ queryKey: statusKey })
    // A renamed key variable changes which variables are Set, and a new token which lists work.
    void client.invalidateQueries({ queryKey: secretsKey })
    // A saved Home Assistant list was applied there too (or not): the toast says which.
    if (changes.some((c) => c.path[0] === 'providers')) refreshHomeAssistant(client)
    if (changes.length || written.saved.length) toastSaved(haApply)
    if (restart) {
      for (const e of restarting) actions.restart.mutate({ params: { path: { name: e.name } } })
    }
    return true
  }

  const save = async () => {
    if (!dirty) return true
    if (store.saving) return false
    store.setSaving(true)
    try {
      return await runSave()
    } catch (e) {
      toast.error('Not saved', { description: errorText(e) })
      return false
    } finally {
      store.setSaving(false)
    }
  }

  return {
    loading: false,
    page: {
      schema: root, value, dirty, changes: edits.length + keys.length, saving: store.saving || secrets.saving,
      update, reset: () => void reset(), save,
    },
  }
}
