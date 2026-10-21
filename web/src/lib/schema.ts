import { ADDITIONAL_PROPERTY_FLAG } from '@rjsf/utils'

// Helpers over the config's JSON Schema. A field may be `hidden` (never drawn: config.yaml only), and
// carries the engines a change `restarts` and, on engine fields, the `drivers` it applies to.

export type JsonSchema = {
  type?: string | string[]
  title?: string
  description?: string
  default?: unknown
  properties?: Record<string, JsonSchema>
  required?: string[]
  additionalProperties?: boolean | JsonSchema
  items?: JsonSchema
  anyOf?: JsonSchema[]
  enum?: unknown[]
  $ref?: string
  $defs?: Record<string, JsonSchema>
  restarts?: string[]
  drivers?: string[]
  [key: string]: unknown
}

export type Path = (string | number)[]

/** A field whose value is the NAME of a variable in data/.env; the form draws a value field for it.
 * `help` says where to get the key; an `optional` key never warns while it is not set. */
export type SecretMeta = { default: string; label: string; help?: string; optional?: boolean }

/** What an extension's `Account` setting says (the SDK's `account` hint). */
export type AccountMeta = { var: string; label: string; help?: string }

/** What prune puts in place of an `Account` setting: Connect, or Connected with Disconnect. */
export type AccountField = { ext: string; field: string; name: string; label: string }

/** What prune adds to a pasted-key field it puts beside a secret's name field. */
export type SecretField = {
  /** The variable the key is stored under: the name field's draft value, else the schema default. */
  name: string
  /** The name field's path from the config root; set to `name` when a key is pasted while it is empty. */
  path: Path
  /** True while the name field is empty, so pasting a key also has to write the name. */
  unnamed: boolean
  /** Not set is fine: the extension works without it. */
  optional: boolean
  /** Its section is switched off, so Not set is no warning yet. */
  quiet: boolean
}

export type PruneOptions = {
  /** Top-level properties handled elsewhere on the page (a list, a switch). */
  omit?: string[]
  /** Keep only fields whose `drivers` include this one (engine forms). Without it the draft's own
   * `driver` beside the field decides. */
  driver?: string
  /** The whole draft config. `when` is checked against it, the rest of a section waits while its
   * `enabled_by` switch is off, and secret fields read their variable name from it. */
  config?: unknown
  /** Where the pruned schema sits in the config, e.g. ['channels', 'discord']. */
  path?: Path
}

/** The key a secret's value field gets next to its name field (`api_key_env` -> `api_key_env__secret`). */
export const SECRET_SUFFIX = '__secret'

type Walk = { path?: Path; slot?: string }

/** Whether a field's `when` holds for the draft: every entry must. A key is a dotted path from the
 * config root with `{slot}` replaced by the enclosing section's slot; null in the allowed values
 * matches a missing value. `cmd_uses` holds when the slot's engine command mentions `{<prefix>`. */
export function holds(when: Record<string, unknown>, config: unknown, slot?: string): boolean {
  return Object.entries(when).every(([key, allowed]) => {
    if (key === 'cmd_uses') {
      if (!slot) return true
      const engine = getIn(config, ['engines', slot])
      const words = [...asList(getIn(engine, ['cmd'])), ...asList(getIn(engine, ['args']))]
      return words.some((w) => typeof w === 'string' && w.includes(`{${String(allowed)}`))
    }
    if (key.includes('{slot}') && !slot) return true
    const value = getIn(config, key.replaceAll('{slot}', slot ?? '').split('.'))
    const values = Array.isArray(allowed) ? allowed : [allowed]
    return values.some((a) => (a === null ? value === undefined || value === null : deepEqual(a, value)))
  })
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** Inline a $ref and collapse `anyOf [T, null]` to T; field metadata wins over the target's. */
export function deref(schema: JsonSchema, root: JsonSchema): JsonSchema {
  let out = schema
  while (out.$ref) {
    const { $ref, ...rest } = out
    const name = $ref.split('/').pop() ?? ''
    out = { ...(root.$defs?.[name] ?? {}), ...rest }
  }
  if (out.anyOf) {
    const alternatives = out.anyOf.filter((a) => a.type !== 'null')
    if (alternatives.length === 1) {
      const rest = { ...out }
      delete rest.anyOf
      out = { ...deref(alternatives[0], root), ...rest }
    }
  }
  return out
}

/** The schema of a nested section, e.g. ['providers', 'home_assistant']. A key that is not a
 * property resolves through additionalProperties, so ['engines', 'item'] is one engine. */
export function section(root: JsonSchema, path: string[]): JsonSchema {
  let current = deref(root, root)
  for (const key of path) {
    const next = current.properties?.[key] ?? current.additionalProperties
    if (!next || typeof next !== 'object') throw new Error(`no schema at ${path.join('.')}`)
    current = deref(next, root)
  }
  return current
}

/** Whether a schema is a keyed map (contacts, environment, volumes): entries under free keys, no
 * fixed fields. Forms draw these as a table with a dialog (KeyedMapField). */
export function isKeyedMap(schema: JsonSchema): boolean {
  return (
    typeof schema.additionalProperties === 'object' &&
    // rjsf stubs a property per existing entry, flagged ADDITIONAL_PROPERTY_FLAG; those are entries too.
    Object.values(schema.properties ?? {}).every((p) => isRecord(p) && (p as Record<symbol, unknown>)[ADDITIONAL_PROPERTY_FLAG] === true)
  )
}

/** Whether a section's feature switch (`enabled_by`) is off in the draft; a missing value is the default. */
function switchedOff(s: JsonSchema, value: unknown, root: JsonSchema): string | undefined {
  const key = typeof s.enabled_by === 'string' ? s.enabled_by : undefined
  const field = key ? s.properties?.[key] : undefined
  if (!key || !field) return undefined
  const current = getIn(value, [key]) ?? deref(field, root).default
  return current === false ? key : undefined
}

/** A self-contained form schema: refs inlined, `hidden` fields dropped, no root title. With the draft
 * config, a field whose `when` does not hold is left out, a section's `enabled_by` switch carries
 * `section_switch`, the fields under it (and every setting of an extension that is off) carry `waits`
 * while it is off, and a `secret` name field gets a value field in its place, drawn even when the name
 * field itself is hidden. */
export function prune(schema: JsonSchema, root: JsonSchema, options: PruneOptions, depth = 0): JsonSchema {
  return pruneAt(schema, root, options, depth, { path: options.path, slot: undefined })
}

function pruneAt(schema: JsonSchema, root: JsonSchema, options: PruneOptions, depth: number, at: Walk): JsonSchema {
  const s = deref(schema, root)
  const out: JsonSchema = { ...s }
  delete out.$defs
  if (depth === 0) delete out.title
  // A form shows some of a section's keys; the data holds the rest, which the server checks.
  if (out.additionalProperties === false) delete out.additionalProperties
  const slot = typeof s.slot === 'string' ? s.slot : at.slot
  const { config } = options
  const value = at.path && config !== undefined ? getIn(config, at.path) : undefined
  if (s.properties) {
    const off = at.path && config !== undefined ? switchedOff(s, value, root) : undefined
    // An extension that is off waits as a whole: its keys do not warn and its lists stay quiet.
    const ext = at.path?.length === 3 && at.path[0] === 'extensions' && at.path[2] === 'settings' ? at.path[1] : undefined
    const extensionOff = ext !== undefined && config !== undefined && getIn(config, ['extensions', ext, 'enabled']) !== true
    const driver = options.driver ?? (typeof getIn(value, ['driver']) === 'string' ? (getIn(value, ['driver']) as string) : undefined)
    const properties: Record<string, JsonSchema> = {}
    for (const [key, child] of Object.entries(s.properties)) {
      const field = deref(child, root)
      if (depth === 0 && options.omit?.includes(key)) continue
      if (config !== undefined && isRecord(field.when) && !holds(field.when, config, slot)) continue
      if (driver && field.drivers && !field.drivers.includes(driver)) continue
      const waits = (off !== undefined && key !== off) || extensionOff
      if (ext !== undefined && isAccount(field.account)) {
        properties[key] = accountField(field, field.account, String(ext), key)
        continue
      }
      const secret = isSecret(field.secret) && at.path ? secretField(field, field.secret, [...at.path, key], config, waits) : null
      if (secret) properties[key + SECRET_SUFFIX] = secret
      // After the pasted key: a secret's name field is hidden, its key is not.
      if (field.hidden === true) continue
      const childPath = at.path ? [...at.path, key] : undefined
      const pruned = pruneAt(field, root, options, depth + 1, { path: childPath, slot })
      if (pruned.properties && Object.keys(pruned.properties).length === 0 && !pruned.additionalProperties) continue
      if (key === s.enabled_by) pruned.section_switch = true
      properties[key] = waits ? { ...pruned, waits: true } : pruned
    }
    // A switch with nothing else shown keeps its own help: nothing visible waits for it.
    const own = typeof s.enabled_by === 'string' ? properties[s.enabled_by] : undefined
    if (own && Object.keys(properties).length === 1) delete own.section_switch
    out.properties = properties
    out.required = s.required?.filter((k) => k in properties)
  }
  if (typeof s.additionalProperties === 'object') {
    // Map entries have no fixed path, so `when` is still checked but a secret gets no pasted-key field.
    out.additionalProperties = pruneAt(s.additionalProperties, root, options, depth + 1, { slot })
  }
  if (s.items) {
    // A list whose schema names a live list (options) picks each item from it.
    const items = s.options && !s.items.options ? { ...s.items, options: s.options, options_query: s.options_query } : s.items
    out.items = pruneAt(items, root, options, depth + 1, { slot })
  }
  return out
}

function isAccount(value: unknown): value is AccountMeta {
  return isRecord(value) && typeof value.var === 'string' && typeof value.label === 'string'
}

/** An `Account` setting as one row, never part of the config: whether it is connected comes from the
 * secrets' status, and Connect or Disconnect acts at once. */
function accountField(field: JsonSchema, meta: AccountMeta, ext: string, key: string): JsonSchema {
  return {
    type: 'string',
    title: field.title ?? meta.label,
    description: field.description,
    help: meta.help ?? field.help,
    group: field.group,
    widget: 'account',
    virtual: true,
    account_field: { ext, field: key, name: meta.var, label: meta.label } satisfies AccountField,
  }
}

function isSecret(value: unknown): value is SecretMeta {
  return isRecord(value) && typeof value.default === 'string' && typeof value.label === 'string'
}

/** The value field drawn for a secret's name field: never part of the config, see SecretField.
 * Its help says where to get the key (the secret's own, else the name field's); it sits in the name
 * field's group. */
function secretField(field: JsonSchema, meta: SecretMeta, path: Path, config: unknown, waits: boolean): JsonSchema {
  const current = getIn(config, path)
  const unnamed = typeof current !== 'string' || current.trim() === ''
  const name = unnamed ? meta.default : (current as string).trim()
  const secret: SecretField = { name, path, unnamed, optional: meta.optional === true, quiet: waits }
  return {
    type: 'string',
    title: meta.label,
    description: 'Kept on this computer and never shown again.',
    help: meta.help ?? field.help,
    group: field.group,
    widget: 'secret',
    virtual: true,
    secret_field: secret,
  }
}

/** Whether a section's form would draw anything, pruned as its SectionForm prunes it. */
export function hasFields(schema: JsonSchema, root: JsonSchema, options: PruneOptions = {}): boolean {
  return Object.keys(prune(schema, root, options).properties ?? {}).length > 0
}

/** A section's other keys, so a form omitting them shows only `keep`; the rest is drawn elsewhere. */
export function allBut(root: JsonSchema, path: string[], keep: string[]): string[] {
  return Object.keys(section(root, path).properties ?? {}).filter((k) => !keep.includes(k))
}

/** A field marked `empty_means_off` (quiet hours, chime, health path, quantization): cleared, it saves
 * '' so the feature stays off, rather than dropping the key and getting the default back. */
function emptyMeansOff(schema: JsonSchema): boolean {
  return schema.empty_means_off === true
}

/** Only what the form fills in by itself: its schema's defaults, all the way down. */
function onlyDefaults(value: unknown, schema: JsonSchema): boolean {
  if (schema.default !== undefined && deepEqual(value, schema.default)) return true
  if (!isRecord(value) || !schema.properties) return false
  const props = schema.properties
  return Object.entries(value).every(([k, v]) => k in props && onlyDefaults(v, props[k]))
}

/** Write a form's data back over the current value, touching only the fields the form showed. A
 * cleared field drops its key (the server's default applies), unless empty means off. A key the saved
 * value leaves out stays out while the form holds only its defaults there: an extension's settings
 * are saved as written, so opening its card must not add them. */
export function applyForm(current: unknown, formData: unknown, formSchema: JsonSchema): unknown {
  if (current === undefined && onlyDefaults(formData, formSchema)) return current
  if (!formSchema.properties || !isRecord(current) || !isRecord(formData)) return formData
  const next: Record<string, unknown> = { ...current }
  for (const [key, child] of Object.entries(formSchema.properties)) {
    if (child.virtual === true) continue
    if (child.properties && isRecord(current[key])) {
      next[key] = applyForm(current[key], formData[key] ?? {}, child)
    } else if (!(key in current) && formData[key] !== undefined && onlyDefaults(formData[key], child)) {
      continue
    } else if (formData[key] === undefined) {
      if (emptyMeansOff(child)) next[key] = ''
      else delete next[key]
    } else {
      next[key] = formData[key]
    }
  }
  return next
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => deepEqual(v, b[i]))
  }
  if (isRecord(a) && isRecord(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    return [...keys].every((k) => deepEqual(a[k], b[k]))
  }
  return false
}

export function getIn(value: unknown, path: Path): unknown {
  let current = value
  for (const key of path) {
    if (Array.isArray(current) && typeof key === 'number') current = current[key]
    else if (isRecord(current)) current = current[String(key)]
    else return undefined
  }
  return current
}

export function setIn<T>(value: T, path: Path, next: unknown): T {
  if (path.length === 0) return next as T
  const [head, ...rest] = path
  if (Array.isArray(value) && typeof head === 'number') {
    const copy = [...value]
    copy[head] = setIn(copy[head], rest, next)
    return copy as T
  }
  const base: Record<string, unknown> = isRecord(value) ? value : {}
  const key = String(head)
  return { ...base, [key]: setIn(base[key], rest, next) } as T
}
