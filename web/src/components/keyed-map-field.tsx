import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { CircleAlert, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import type { FieldProps } from '@rjsf/utils'
import type { Options } from '@/api/client'
import { ChoiceName, ChoicePicture, Combobox, type Choice } from '@/components/combobox'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { ItemDialog } from '@/components/item-dialog'
import { KindIcon } from '@/components/kind-icon'
import { StatusTip } from '@/components/status-tip'
import { DataTable, type Column } from '@/components/templates/list-page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useOptions, type OptionsQuery } from '@/hooks/use-options'
import { isRecord, type JsonSchema } from '@/lib/schema'

type MapSchema = JsonSchema & {
  help?: string
  key_title?: string
  key_description?: string
  value_title?: string
  key_options?: string
  key_options_query?: OptionsQuery
  icon?: string
}

type Row = { key: string; value: unknown }
type Editing = { key: string; original?: string; value: unknown }

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

/** A primitive value in a row: its choice from the live list (cover or icon, label, the raw value muted)
 * when known, else the value itself. */
function ValueCell({ value, choice }: { value: unknown; choice?: Choice }) {
  const text = value === undefined || value === null ? '' : String(value)
  if (!choice) return <span className="break-words">{text}</span>
  return (
    <div className="flex items-center gap-2">
      <ChoicePicture choice={choice} className="size-8" />
      <div className="flex flex-col">
        <ChoiceName text={choice.hint ?? choice.label} />
        <span className="text-xs break-words text-muted-foreground">{text}</span>
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
      <span className="break-words">{value}</span>
      <StatusTip icon={CircleAlert} className="text-warning" label={found.problem ?? 'Not found'} />
    </div>
  )
}

/** A field's value picked from a live list, by the list's label: a sensor's name rather than its id. */
function PickedLabel({ schema, value }: { schema: JsonSchema; value: string }) {
  const { choices } = useOptions(String(schema.options), schema.options_query as OptionsQuery | undefined)
  return <>{choices.find((c) => c.value === value)?.label ?? value}</>
}

/** An object value in a row: its first few simple fields, joined; a picked one by its label. */
function Summary({ value, schema }: { value: unknown; schema: JsonSchema }) {
  if (!isRecord(value)) return null
  const shown = Object.entries(value)
    .filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v) && v !== '')
    .slice(0, 3)
  return (
    <span className="break-words">
      {shown.map(([k, v], i) => (
        <Fragment key={k}>
          {i > 0 ? ', ' : null}
          {typeof schema.properties?.[k]?.options === 'string' && typeof v === 'string' ? (
            <PickedLabel schema={schema.properties[k]} value={v} />
          ) : (
            String(v)
          )}
        </Fragment>
      ))}
    </span>
  )
}

type Listed = { choices: Options['choices']; loading: boolean; problem?: string }
const NONE: Listed = { choices: [], loading: false }

function Loaded({ name, query, children }: { name: string; query?: OptionsQuery; children: (list: Listed) => ReactNode }) {
  return children(useOptions(name, query))
}

/** A live list when the schema names one; nothing is fetched otherwise. */
function OptionsFor({ name, query, children }: { name?: unknown; query?: OptionsQuery; children: (list: Listed) => ReactNode }) {
  if (typeof name !== 'string' || name === '') return children(NONE)
  return (
    <Loaded name={name} query={query}>
      {children}
    </Loaded>
  )
}

/** Every object of entries in a settings form (contacts, engine environment, volumes): a table with
 * one row per entry, its name a button opening the entry's dialog, Add for a new one, trash with Undo.
 * Titles, help, key list and icon come from the schema (`key_title`, `value_title`, `key_options` with
 * `key_options_query`, `icon`; value `options` and `pattern` on additionalProperties). */
export function KeyedMapField(props: FieldProps) {
  const schema = props.schema as MapSchema
  const valueSchema = valueSchemaOf(schema)
  return (
    <OptionsFor name={schema.key_options} query={schema.key_options_query}>
      {(keys) => (
        <OptionsFor name={valueSchema.options} query={valueSchema.options_query as OptionsQuery | undefined}>
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
  const knownKeys = new Map(keyChoices.map((c) => [c.value, c]))
  const valueChoices = valueOptions.choices
  const known = new Map(valueChoices.map((c) => [c.value, c]))
  const [editing, setEditing] = useState<Editing | null>(null)
  const locked = Boolean(disabled || readonly)
  const listed = typeof valueSchema.options === 'string' ? valueSchema.options : undefined
  // A list that could not be fetched (no bot token yet) leaves a plain text box for the value.
  const pickable = listed !== undefined && !(valueOptions.problem && !valueOptions.loading && valueChoices.length === 0)
  // So does a key list (an extension that is not running), saying why unless the whole extension is off.
  const keysPickable = Boolean(schema.key_options) && !(keyOptions.problem && !keyOptions.loading && keyChoices.length === 0)
  const keyHelp = schema.key_options && !keysPickable && !schema.waits ? keyOptions.problem : undefined

  // Undo restores into the entries as they are when it is clicked, not as they were when removed.
  const latest = useRef(entries)
  useEffect(() => {
    latest.current = entries
  })
  const set = (next: Record<string, unknown>) => onChange(next, fieldPathId.path)

  const rows: Row[] = Object.entries(entries).map(([key, value]) => ({ key, value }))
  const columns: Column<Row>[] = [
    {
      key: 'key',
      title: keyTitle,
      className: 'whitespace-normal',
      render: (r) => {
        const choice = knownKeys.get(r.key)
        const name = (
          <Button
            type="button"
            variant="link"
            className="h-auto p-0 text-left break-words whitespace-normal text-foreground"
            disabled={locked}
            onClick={() => setEditing({ key: r.key, original: r.key, value: r.value })}
          >
            <ChoiceName text={choice?.label ?? r.key} />
          </Button>
        )
        if (!choice) return name
        return (
          <div className="flex items-center gap-2">
            <ChoicePicture choice={choice} className="size-8" />
            <div className="flex flex-col items-start">
              {name}
              {choice.hint ? <span className="text-xs text-muted-foreground">{choice.hint}</span> : null}
            </div>
          </div>
        )
      },
    },
    {
      key: 'value',
      title: valueTitle,
      className: 'whitespace-normal',
      render: (r) => {
        if (!primitive) return <Summary value={r.value} schema={valueSchema} />
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
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
        <KindIcon name={schema.icon} />
        <span className="text-sm leading-none font-medium">{schema.title}</span>
        <InfoTip text={schema.description} label={schema.title} />
        <Button type="button" variant="outline" size="sm" className="ml-auto" disabled={locked} onClick={() => setEditing({ key: '', value: primitive ? '' : {} })}>
          <Plus />
          {`Add ${keyTitle.toLowerCase()}`}
        </Button>
      </div>
      {schema.help ? <p className="text-sm text-muted-foreground">{schema.help}</p> : null}
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.key}
        actions={(r) => (
          <IconButton type="button" label={`Remove ${r.key}`} disabled={locked} onClick={() => remove(r.key)}>
            <Trash2 />
          </IconButton>
        )}
      />
      <ItemDialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing?.original ?? `Add ${keyTitle.toLowerCase()}`}
        keyLabel={keyTitle}
        keyInfo={schema.key_description ?? schema.description}
        keyValue={editing?.key ?? ''}
        onKeyChange={(key) => setEditing((e) => e && { ...e, key })}
        keyHelp={keyHelp}
        keyInput={
          keysPickable ? (
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
              <InfoTip text={valueSchema.description ?? schema.description} label={valueTitle} />
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
              <div className="flex flex-col gap-2">
                <Input
                  id="item-value"
                  value={value === undefined || value === null ? '' : String(value)}
                  aria-invalid={badValue}
                  onChange={(e) => setEditing((x) => x && { ...x, value: e.target.value })}
                />
                {listed && valueOptions.problem ? <p className="text-sm text-muted-foreground">{valueOptions.problem}</p> : null}
              </div>
            )}
            {badValue ? <span className="text-sm text-destructive">Not a valid {valueTitle.toLowerCase()}</span> : null}
          </div>
        ) : null}
      </ItemDialog>
    </div>
  )
}
