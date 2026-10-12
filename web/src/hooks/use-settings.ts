import { createContext, useCallback, useContext, useState, type Dispatch, type SetStateAction } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'react-router'
import { toast } from 'sonner'
import { $api, errorText, fetchClient, type Config, type EngineStatus, type PersonaRename, type SecretStatus } from '@/api/client'
import { clearFieldErrors, showFieldErrors } from '@/api/field-errors'
import { configKey, patchConfig, useConfig, useSchema, type ConfigChange, type PatchOutcome } from '@/api/config'
import { engineLabel, restartCost, statusKey, useEngineActions } from '@/api/status'
import { useConfirm } from '@/hooks/use-confirm'
import { useDraft } from '@/hooks/use-draft'
import { DOWNLOAD_COST, mustDownload, optionsParams } from '@/hooks/use-options'
import { refreshHomeAssistant, toastSaved, type HAApply } from '@/hooks/use-home-assistant'
import { secretRestarts, secretsKey, useSecretStatus, useSecretsDraft } from '@/hooks/use-secrets'
import { capitalize, humanize, joinNames } from '@/lib/format'
import { presetSwitches } from '@/lib/presets'
import { deepEqual, deref, getIn, hasBasicFields, isRecord, section, type JsonSchema, type Path } from '@/lib/schema'

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

const startsWith = (path: Path, prefix: Path) => prefix.every((step, i) => path[i] === step)

// Which page shows which part of the config (pages/*.tsx), for the sidebar's dots and the confirms.

export type SettingsPageInfo = { to: string; label: string; sections: string[][] }

export const settingsPages: SettingsPageInfo[] = [
  { to: '/assistant', label: 'Assistant', sections: [['assistant'], ['region'], ['memory']] },
  { to: '/brain', label: 'Brain', sections: [['llm'], ['engines', 'llm']] },
  { to: '/voice', label: 'Voice', sections: [['tts'], ['stt'], ['vad'], ['engines', 'tts']] },
  { to: '/channels', label: 'Channels', sections: [['channels'], ['announce']] },
  { to: '/home-assistant', label: 'Home Assistant', sections: [['providers']] },
  { to: '/extensions', label: 'Extensions', sections: [['extensions'], ['speak_up']] },
  { to: '/engines', label: 'Engines', sections: [['engines']] },
  { to: '/system', label: 'System', sections: [['logging'], ['control'], ['version']] },
]

/** Every page that shows a path, most specific first (engines.llm is on Brain and on Engines). */
export function pagesOf(path: Path): SettingsPageInfo[] {
  return settingsPages.filter((p) => p.sections.some((s) => startsWith(path, s) || startsWith(s, path)))
}

const pageAt = (pathname: string) => settingsPages.find((p) => pathname.startsWith(p.to))

/** A field's name for a person, from the schema titles: "Voice", "Contacts: Mom", "llm: Args". */
export function fieldLabel(root: JsonSchema, path: Path): string {
  let current: JsonSchema | undefined = deref(root, root)
  const parts: string[] = []
  let keyAt = -1
  let switchAt = -1
  for (const step of path) {
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
      // A section switch ("Enabled") is named with its section, "Announce: Enabled".
      switchAt = current.enabled_by === step ? parts.length : -1
      current = deref(property, root)
      // A section's own title is its model's class name ("AnnounceConfig"); its key reads better.
      parts.push(property.title ?? (property.$ref ? humanize(step) : (current.title ?? humanize(step))))
    } else if (isRecord(current.additionalProperties)) {
      current = deref(current.additionalProperties as JsonSchema, root)
      keyAt = parts.length
      parts.push(step)
    } else {
      current = undefined
      parts.push(humanize(step))
    }
  }
  const last = parts.length - 1
  if (last < 0) return 'Config'
  if (keyAt === last && last > 0) return `${parts[last - 1]}: ${parts[last]}`
  if (keyAt >= 0 && keyAt < last) return `${parts[keyAt]}: ${parts[last]}`
  if (switchAt === last && last > 0) return `${parts[last - 1]}: ${parts[last]}`
  return parts[last]
}

/** The config paths that name a key, so it counts on their page; none for a name nothing uses yet. */
const secretPaths = (name: string, byName: Map<string, SecretStatus>): Path[] => byName.get(name)?.used_by ?? []

/** A pasted key by its field's label ("Bot token"), else its variable name. */
function keyLabel(root: JsonSchema, path: Path, name: string): string {
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

/** "Voice: Voice, Speed" per page, for the confirms. */
function summary(root: JsonSchema, edits: ConfigChange[], keys: string[], byName: Map<string, SecretStatus>,
                 switches: { line: string; paths: string[][] }[] = []): string[] {
  const groups = new Map<string, Set<string>>()
  const add = (page: string, label: string) => groups.set(page, (groups.get(page) ?? new Set()).add(label))
  const switched = (path: Path) => switches.some((w) => w.paths.some((p) => p.every((k, i) => path[i] === k)))
  for (const c of edits) {
    if (switched(c.path)) continue
    const page = pagesOf(c.path)[0]?.label ?? 'Other'
    const label = fieldLabel(root, c.path)
    add(page, label.startsWith(`${page}: `) ? label.slice(page.length + 2) : label)     // not "Engines: Engines: stt"
  }
  for (const name of keys) {
    const path = secretPaths(name, byName)[0]
    // A key the saved config does not name yet (a new MCP server's token) is listed by its name.
    add((path && pagesOf(path)[0]?.label) || 'Keys', path ? keyLabel(root, path, name) : name)
  }
  return [...switches.map((w) => w.line), ...[...groups].map(([page, labels]) => `${page}: ${[...labels].join(', ')}`)]
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
  saving: boolean
  advanced: boolean
  setAdvanced: (advanced: boolean) => void
  update: (fn: (current: Config) => Config) => void
  reset: () => void
  save: () => Promise<void>
}

type Loading = { loading: true; page: null }
type Ready = { loading: false; page: SettingsState }

/** Whether this save's brain restart downloads the new model first. Asks winstond, never the Hub. */
async function needsDownload(changes: ConfigChange[], config: Config, restartable: EngineStatus[]): Promise<boolean> {
  if (!restartable.some((e) => e.name === 'llm') || !changes.some((c) => c.path.join('.') === 'llm.model')) return false
  const { data } = await fetchClient.GET('/api/options/{name}', optionsParams('llm_models', config))
  return mustDownload(data, config.llm.model)
}

/** A page's view of the shared draft plus the save flow: a dry-run patch of only the changed paths,
 * confirm what changes and restarts, save with the version the edits started from, write pending
 * keys, restart. `paths` are the sections the page shows; a page with no basic field opens Advanced. */
export function useSettings(paths: string[][]): Loading | Ready {
  const client = useQueryClient()
  const config = useConfig()
  const schema = useSchema()
  const actions = useEngineActions()
  const status = $api.useQuery('get', '/api/status')
  const presets = $api.useQuery('get', '/api/presets')
  const secrets = useSecretsDraft()
  const { byName } = useSecretStatus()
  const ask = useConfirm()
  const { pathname } = useLocation()
  const { draft, setDraft } = useDraft()
  const store = useDraftStore()
  const [advancedChoice, setAdvancedChoice] = useState<boolean | null>(null)

  const update = useCallback((fn: (current: Config) => Config) => setDraft((d) => (d ? fn(d) : d)), [setDraft])

  const base = config.data
  if (!base || !schema.data) return { loading: true, page: null }
  const root = schema.data
  const value = draft ?? base
  const { edits } = store
  const keys = Object.keys(secrets.pending)
  const dirty = edits.length > 0 || keys.length > 0
  const anyBasic = paths.some((p) => hasBasicFields(section(root, p), root))
  const advanced = advancedChoice ?? !anyBasic
  const current = pageAt(pathname)
  const onCurrent = (path: Path) => !!current && pagesOf(path).includes(current)
  const keyHere = (name: string) => secretPaths(name, byName).some(onCurrent)
  const elsewhere = edits.some((c) => !onCurrent(c.path)) || keys.some((n) => !keyHere(n))

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
      lines: summary(root, edits, keys, byName),
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
      const what = conflict.paths.length ? conflict.paths.map((p) => p.join('.')).join(', ') : 'the config'
      toast.warning(`Winston changed ${what} meanwhile; your edits are kept on top`, {
        description: 'Check them and save again.',
      })
      return
    }
    const errors = outcome.status === 422 ? showFieldErrors(outcome.error, { schema: root, setAdvanced: setAdvancedChoice }) : []
    if (errors.length) {
      const lines = errors.map((e) =>
        e.loc.length ? `${pagesOf(e.loc)[0]?.label ?? 'Config'}, ${fieldLabel(root, e.loc)}: ${e.msg}` : e.msg,
      )
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
    if (changes.length) {
      const dry = await patchConfig(changes, { version, dryRun: true })
      if (!dry.ok) return refused(dry)
      restarts = dry.result.restarts
      rename = dry.result.persona_rename
    }
    restarts = [...new Set([...restarts, ...secretRestarts(keys, byName)])]

    const engines = status.data?.engines ?? []
    const restartable = engines.filter((e) => restarts.includes(e.name) && e.state !== 'stopped' && e.state !== 'error')
    // An engine the config gains starts on save (winstond starts what it did not run before).
    const starting = [...new Set(changes.filter((c) => c.path[0] === 'engines').map((c) => String(c.path[1])))]
      .filter((n) => value.engines?.[n] && value.engines[n].enabled !== false && !engines.some((e) => e.name === n))
    const later = restarts.some((name) => !engines.some((e) => e.name === name))
    const download = await needsDownload(changes, value, restartable)
    // An engine the config no longer has (hearing switched back to Parakeet): its "restart" stops it for good.
    const stopping = restartable.filter((e) => e.kind === 'engine' && !value.engines?.[e.name])
    const restarting = restartable.filter((e) => !stopping.includes(e))
    const costs = [
      ...stopping.map((e) => `${capitalize(engineLabel(e))}: stops, nothing uses it now`),
      ...restarting.map((e) =>
        e.name === 'llm' && download
          ? `${capitalize(engineLabel(e))}: ${DOWNLOAD_COST}`
          : `${capitalize(engineLabel(e))}: ${restartCost(e)}`,
      ),
      ...starting.map((n) => `The ${n} engine: starts when saved`),
      ...(later ? ['Some changes apply on the next winston up'] : []),
    ]

    // A rename whose old name his persona says: the same confirm offers to change it there too.
    let renamePersona = false
    const check = rename
      ? {
          label: `His persona says ${rename.old} in ${rename.places} ${rename.places === 1 ? 'place' : 'places'}. Change ${rename.places === 1 ? 'it' : 'them'} to ${rename.new}?`,
          detail: rename.lines.map((line) => (line.length > 72 ? `${line.slice(0, 71)}…` : line)),
          checked: true,
          answer: (on: boolean) => { renamePersona = on },
        }
      : undefined
    let restart = false
    if (costs.length || elsewhere || check) {
      const choice = await ask({
        // A rename leads (its restarts are in the lines and the choices), as Save and restart would bury it.
        title: rename
          ? `Rename him to ${rename.new}?`
          : restarting.length
            ? `Save and restart ${joinNames(restarting.map(engineLabel))}?`
            : stopping.length
              ? `Save and stop ${joinNames(stopping.map(engineLabel))}?`
              : starting.length
              ? `Save and start the ${joinNames(starting)} engine?`
              : 'Save these changes?',
        lines: [...summary(root, changes, keys, byName, presetSwitches(base, value, presets.data)), ...costs],
        check,
        choices: restartable.length
          ? [
              { label: 'Save only', value: 'save' },
              { label: restarting.length ? 'Save and restart' : 'Save and stop', value: 'restart' },
            ]
          : [{ label: 'Save', value: 'save' }],
      })
      if (!choice) return
      restart = choice === 'restart'
    }

    // The config first, so a variable name a key is pasted under is saved before the key.
    let haApply: HAApply | null | undefined
    if (changes.length) {
      const saved = await patchConfig(changes, { version, client, renamePersona })
      if (!saved.ok) return refused(saved)
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
      for (const e of restartable) actions.restart.mutate({ params: { path: { name: e.name } } })
    }
  }

  const save = async () => {
    if (!dirty || store.saving) return
    store.setSaving(true)
    try {
      await runSave()
    } finally {
      store.setSaving(false)
    }
  }

  return {
    loading: false,
    page: {
      schema: root, value, dirty, saving: store.saving || secrets.saving, advanced, setAdvanced: setAdvancedChoice,
      update, reset: () => void reset(), save,
    },
  }
}
