import { useEffect, useRef, useState, type ReactNode } from 'react'
import { CircleAlert, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import type { FieldProps } from '@rjsf/utils'
import { Combobox, type Choice } from '@/components/combobox'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { ItemDialog } from '@/components/item-dialog'
import { KindIcon } from '@/components/kind-icon'
import { StatusTip } from '@/components/status-tip'
import { DataTable, type Column } from '@/components/templates/list-page'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useOptions } from '@/hooks/use-options'
import { isRecord, type JsonSchema } from '@/lib/schema'

type MapSchema = JsonSchema & {
  key_title?: string
  key_description?: string
  value_title?: string
  key_options?: string
  icon?: string
}

type Row = { key: string; value: unknown }
type Editing = { key: string; original?: string; value: unknown }

/** "Contacts" -> "contact", for "New contact"; otherwise the key's title ("New variable"). */
function newTitle(schema: MapSchema, keyTitle: string): string {
  const title = schema.title ?? ''
  const one = /[^s]s$/.test(title) ? title.slice(0, -1) : keyTitle
  return `New ${one.toLowerCase()}`
}

function valueSchemaOf(schema: MapSchema): JsonSchema {
  return (typeof schema.additionalProperties === 'object' ? schema.additionalProperties : {}) as JsonSchema
}

function isPrimitive(schema: JsonSchema): boolean {
  return schema.type !== 'object' && !schema.properties
}

function matches(schema: JsonSchema, value: unknown): boolean {
  if (typeof schema.pattern !== 'string' || typeof value !== 'string') return true
  return new RegExp(schema.pattern).test(value)
}

/** A primitive value in a row: its choice from the live list (icon, label, the raw value muted) when
 * known, else the value itself. */
function ValueCell({ value, choice }: { value: unknown; choice?: Choice }) {
  const text = value === undefined || value === null ? '' : String(value)
  if (!choice) return <span className="break-all">{text}</span>
  return (
    <div className="flex items-center gap-2">
      <KindIcon name={choice.icon} className="text-muted-foreground" />
      <div className="flex flex-col">
        <span>{choice.hint ?? choice.label}</span>
        <span className="text-xs break-all text-muted-foreground">{text}</span>
      </div>
    </div>
  )
}

/** A value the live list does not hold (a pasted id): asked for by itself with `q`, and shown as its
 * choice when found, else as the value with a warning icon saying why. */
function LookedUp({ name, value }: { name: string; value: string }) {
  const found = useOptions(name, { q: value }, value !== '')
  const choice = found.choices.find((c) => c.value === value)
  if (choice || found.loading) return <ValueCell value={value} choice={choice} />
  return (
    <div className="flex items-center gap-2">
      <span className="break-all">{value}</span>
      <StatusTip icon={CircleAlert} className="text-warning" label={found.problem ?? 'Not found'} />
    </div>
  )
}

/** An object value in a row: its first few simple fields, joined. */
function summary(value: unknown): string {
  if (!isRecord(value)) return ''
  return Object.values(value)
    .filter((v) => ['string', 'number', 'boolean'].includes(typeof v) && v !== '')
    .slice(0, 3)
    .join(', ')
}

type Listed = { choices: Choice[]; loading: boolean; problem?: string }
const NONE: Listed = { choices: [], loading: false }

function Loaded({ name, children }: { name: string; children: (list: Listed) => ReactNode }) {
  return children(useOptions(name))
}

/** A live list when the schema names one; nothing is fetched otherwise. */
function OptionsFor({ name, children }: { name?: unknown; children: (list: Listed) => ReactNode }) {
  if (typeof name !== 'string' || name === '') return children(NONE)
  return <Loaded name={name}>{children}</Loaded>
}

/** Every object of entries in a settings form (contacts, engine environment, volumes): a table with
 * one row per entry, a dialog to add or edit one, trash with Undo. Titles, key list and icon come from
 * the schema (`key_title`, `value_title`, `key_options`, `icon`; value `options` and `pattern` on
 * additionalProperties). */
export function KeyedMapField(props: FieldProps) {
  const schema = props.schema as MapSchema
  return (
    <OptionsFor name={schema.key_options}>
      {(keys) => (
        <OptionsFor name={valueSchemaOf(schema).options}>
          {(values) => <MapTable {...props} keyOptions={keys} valueOptions={values} />}
        </OptionsFor>
      )}
    </OptionsFor>
  )
}

function MapTable({
  schema: raw, formData, onChange, fieldPathId, disabled, readonly, keyOptions, valueOptions,
}: FieldProps & { keyOptions: Listed; valueOptions: Listed }) {
  const schema = raw as MapSchema
  const valueSchema = valueSchemaOf(schema)
  const primitive = isPrimitive(valueSchema)
  const keyTitle = schema.key_title ?? 'Name'
  const valueTitle = schema.value_title ?? valueSchema.title ?? 'Value'
  const entries: Record<string, unknown> = isRecord(formData) ? formData : {}
  const keyChoices = keyOptions.choices
  const valueChoices = valueOptions.choices
  const known = new Map(valueChoices.map((c) => [c.value, c]))
  const [editing, setEditing] = useState<Editing | null>(null)
  const locked = Boolean(disabled || readonly)
  const listed = typeof valueSchema.options === 'string' ? valueSchema.options : undefined
  // A list that could not be fetched (no bot token yet) leaves a plain text box for the value.
  const pickable = listed !== undefined && !(valueOptions.problem && !valueOptions.loading && valueChoices.length === 0)

  // Undo restores into the entries as they are when it is clicked, not as they were when removed.
  const latest = useRef(entries)
  useEffect(() => {
    latest.current = entries
  })
  const set = (next: Record<string, unknown>) => onChange(next, fieldPathId.path)

  const rows: Row[] = Object.entries(entries).map(([key, value]) => ({ key, value }))
  const columns: Column<Row>[] = [
    { key: 'key', title: keyTitle, className: 'whitespace-normal', render: (r) => <span className="font-medium break-all">{r.key}</span> },
    {
      key: 'value',
      title: valueTitle,
      className: 'whitespace-normal',
      render: (r) => {
        if (!primitive) return <span>{summary(r.value)}</span>
        const choice = known.get(String(r.value))
        // Only look a value up while the list itself works; without a token only the value shows.
        if (!choice && listed && !valueOptions.problem && !valueOptions.loading && typeof r.value === 'string')
          return <LookedUp name={listed} value={r.value} />
        return <ValueCell value={r.value} choice={choice} />
      },
    },
  ]

  const remove = (key: string) => {
    const removed = entries[key]
    const next = { ...entries }
    delete next[key]
    set(next)
    toast(`Removed ${key}`, {
      action: { label: 'Undo', onClick: () => set({ ...latest.current, [key]: removed }) },
    })
  }

  const commit = () => {
    if (!editing) return
    const next = { ...entries }
    if (editing.original !== undefined && editing.original !== editing.key.trim()) delete next[editing.original]
    next[editing.key.trim()] = editing.value
    set(next)
    setEditing(null)
  }

  const value = editing?.value
  const valueOk = primitive
    ? value !== undefined && value !== '' && matches(valueSchema, value)
    : true
  const badValue = primitive && value !== undefined && value !== '' && !matches(valueSchema, value)
  const idBase = fieldPathId.$id

  // A picked value names someone: an empty key takes their name (a contact from a Discord user).
  const pickValue = (v: string) =>
    setEditing((e) => e && { ...e, value: v, key: e.key.trim() ? e.key : (known.get(v)?.label ?? e.key) })

  return (
    <div id={idBase} className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <KindIcon name={schema.icon} />
        <span className="text-sm leading-none font-medium">{schema.title}</span>
        <InfoTip text={schema.description} />
        <span className="flex-1" />
        <IconButton
          label={newTitle(schema, keyTitle).replace(/^New/, 'Add')}
          type="button"
          variant="outline"
          disabled={locked}
          onClick={() => setEditing({ key: '', value: primitive ? '' : {} })}
        >
          <Plus />
        </IconButton>
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.key}
        onRowClick={locked ? undefined : (r) => setEditing({ key: r.key, original: r.key, value: r.value })}
        actions={(r) => (
          <IconButton type="button" label="Remove" disabled={locked} onClick={() => remove(r.key)}>
            <Trash2 />
          </IconButton>
        )}
      />
      <ItemDialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing?.original ?? newTitle(schema, keyTitle)}
        keyLabel={keyTitle}
        keyInfo={schema.key_description ?? schema.description}
        keyValue={editing?.key ?? ''}
        onKeyChange={(key) => setEditing((e) => e && { ...e, key })}
        keyInput={
          schema.key_options ? (
            <Combobox
              id="item-key"
              value={editing?.key}
              choices={keyChoices.filter((c) => !(c.value in entries) || c.value === editing?.original)}
              custom
              loading={keyOptions.loading}
              problem={keyOptions.problem}
              onChange={(key) => setEditing((e) => e && { ...e, key })}
            />
          ) : undefined
        }
        existing={Object.keys(entries)}
        original={editing?.original}
        canSubmit={(editing?.key.trim() ?? '') !== '' && valueOk}
        schema={primitive ? undefined : valueSchema}
        value={primitive ? undefined : value}
        onChange={primitive ? undefined : (next) => setEditing((e) => e && { ...e, value: next })}
        onSubmit={commit}
      >
        {primitive ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-1.5">
              <Label htmlFor="item-value">{valueTitle}</Label>
              <InfoTip text={valueSchema.description ?? schema.description} />
            </div>
            {pickable ? (
              <Combobox
                id="item-value"
                value={typeof value === 'string' ? value : ''}
                choices={valueChoices}
                custom
                loading={valueOptions.loading}
                problem={valueOptions.problem}
                onChange={pickValue}
              />
            ) : (
              <div className="flex items-center gap-2">
                <Input
                  id="item-value"
                  value={value === undefined || value === null ? '' : String(value)}
                  aria-invalid={badValue}
                  onChange={(e) => setEditing((x) => x && { ...x, value: e.target.value })}
                />
                {listed && valueOptions.problem ? <StatusTip icon={CircleAlert} className="text-warning" label={valueOptions.problem} /> : null}
              </div>
            )}
            {badValue ? <span className="text-sm text-destructive">Not a valid {valueTitle.toLowerCase()}</span> : null}
          </div>
        ) : null}
      </ItemDialog>
    </div>
  )
}
