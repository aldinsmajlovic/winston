// Helpers over the config's JSON Schema. Every field carries `level` (basic | advanced), the
// engines a change `restarts`, and, on engine fields, the `drivers` it applies to.

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
  level?: 'basic' | 'advanced'
  restarts?: string[]
  drivers?: string[]
  [key: string]: unknown
}

export type PruneOptions = {
  advanced: boolean
  /** Top-level properties handled elsewhere on the page (a list, a switch). */
  omit?: string[]
  /** Keep only fields whose `drivers` include this one (engine forms). */
  driver?: string
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
 * property resolves through additionalProperties, so ['tools', 'sensors', 'item'] is one sensor. */
export function section(root: JsonSchema, path: string[]): JsonSchema {
  let current = deref(root, root)
  for (const key of path) {
    const next = current.properties?.[key] ?? current.additionalProperties
    if (!next || typeof next !== 'object') throw new Error(`no schema at ${path.join('.')}`)
    current = deref(next, root)
  }
  return current
}

function isAdvanced(schema: JsonSchema): boolean {
  return schema.level === 'advanced'
}

/** A self-contained form schema: refs inlined, advanced fields dropped unless asked for, hints
 * only on advanced fields, no root title. */
export function prune(schema: JsonSchema, root: JsonSchema, options: PruneOptions, depth = 0): JsonSchema {
  const s = deref(schema, root)
  const out: JsonSchema = { ...s }
  delete out.$defs
  if (depth === 0) delete out.title
  if (!isAdvanced(s)) delete out.description
  if (s.properties) {
    const properties: Record<string, JsonSchema> = {}
    for (const [key, child] of Object.entries(s.properties)) {
      const field = deref(child, root)
      if (depth === 0 && options.omit?.includes(key)) continue
      if (!options.advanced && isAdvanced(field)) continue
      if (options.driver && field.drivers && !field.drivers.includes(options.driver)) continue
      const pruned = prune(field, root, options, depth + 1)
      if (pruned.properties && Object.keys(pruned.properties).length === 0 && !pruned.additionalProperties) continue
      properties[key] = pruned
    }
    out.properties = properties
    out.required = s.required?.filter((k) => k in properties)
  }
  if (typeof s.additionalProperties === 'object') {
    out.additionalProperties = prune(s.additionalProperties, root, options, depth + 1)
  }
  if (s.items) out.items = prune(s.items, root, options, depth + 1)
  return out
}

/** True when a section has nothing to show at the basic level. */
export function hasBasicFields(schema: JsonSchema, root: JsonSchema): boolean {
  return Object.keys(prune(schema, root, { advanced: false }).properties ?? {}).length > 0
}

/** Write a form's data back over the current value, touching only the fields the form showed. */
export function applyForm(current: unknown, formData: unknown, formSchema: JsonSchema): unknown {
  if (!formSchema.properties || !isRecord(current) || !isRecord(formData)) return formData
  const next: Record<string, unknown> = { ...current }
  for (const [key, child] of Object.entries(formSchema.properties)) {
    if (child.properties && isRecord(current[key])) {
      next[key] = applyForm(current[key], formData[key] ?? {}, child)
    } else if (formData[key] === undefined) {
      delete next[key]
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

export function getIn(value: unknown, path: string[]): unknown {
  let current = value
  for (const key of path) {
    if (!isRecord(current)) return undefined
    current = current[key]
  }
  return current
}

export function setIn<T>(value: T, path: string[], next: unknown): T {
  if (path.length === 0) return next as T
  const [head, ...rest] = path
  const base: Record<string, unknown> = isRecord(value) ? value : {}
  return { ...base, [head]: setIn(base[head], rest, next) } as T
}
