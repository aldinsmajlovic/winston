import { createContext, Fragment, useContext, useState, type ReactNode } from 'react'
import { getDefaultRegistry } from '@rjsf/core'
import Form, { Widgets } from '@rjsf/shadcn'
import validator from '@rjsf/validator-ajv8'
import {
  getInputProps,
  titleId,
  type ArrayFieldItemTemplateProps,
  type ArrayFieldTemplateProps,
  type BaseInputTemplateProps,
  type ErrorSchema,
  type FieldProps,
  type FieldTemplateProps,
  type ObjectFieldTemplatePropertyType,
  type ObjectFieldTemplateProps,
  type RJSFSchema,
  type TitleFieldProps,
  type UiSchema,
  type WidgetProps,
} from '@rjsf/utils'
import { CircleAlert, Plus, Trash2 } from 'lucide-react'
import { Combobox, type Choice } from '@/components/combobox'
import { IconButton } from '@/components/icon-button'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { InfoTip } from '@/components/info-tip'
import { KindIcon } from '@/components/kind-icon'
import { HintWordsField } from '@/components/hint-words-field'
import { KeyedMapField } from '@/components/keyed-map-field'
import { AccountField } from '@/components/account-field'
import { SecretField } from '@/components/secret-field'
import { Adornments } from '@/lib/adornments'
import { humanize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useOptions, type OptionsQuery } from '@/hooks/use-options'
import { type AccountField as AccountFieldMeta, deepEqual, isKeyedMap, type JsonSchema, type Path, type SecretField as SecretMeta } from '@/lib/schema'

// The theme's own checkbox list, for multiple choice fields.
const CheckboxesWidget = Widgets.CheckboxesWidget

const uiSchema: UiSchema = { 'ui:submitButtonOptions': { norender: true } }

/** What a SectionForm hands its fields beyond their own value. */
export type FormContext = {
  /** Set a value anywhere in the config draft (a pasted key writes its variable name). */
  setAt?: (path: Path, value: unknown) => void
  /** The whole config draft, for a field that reads a sibling (hint words read their removed list). */
  config?: unknown
  /** Errors about a nested object, by the id of its title, shown beside the title. */
  titleErrors?: Record<string, string[]>
}

type Meta = JsonSchema & {
  options?: string
  options_query?: OptionsQuery
  labels?: Record<string, string>
  icons?: Record<string, string>
  widget?: string
  help?: string // one visible line under the control; a section's lead line under its heading
  unit?: string // drawn after a number: "s", "min", "MB"
  group?: string // fields sharing one follow one heading
  section_switch?: boolean // the switch its section's other fields wait for
  waits?: boolean // under a section switch that is off
  secret_field?: SecretMeta
  account_field?: AccountFieldMeta
  placeholder?: string // what an empty picker means
}

/** A field's visible explanation, muted under it. */
function Help({ text }: { text?: string }) {
  return text ? <p className="text-sm text-muted-foreground">{text}</p> : null
}

function enumChoices(schema: Meta): Choice[] {
  const values = (schema.enum ?? (schema.const !== undefined ? [schema.const] : [])) as string[]
  return values.map((v) => ({ value: v, label: schema.labels?.[v] ?? humanize(String(v)), icon: schema.icons?.[v] }))
}

const WAITING = 'Off. These settings wait until you switch it on.'

/** Switch, label and (i), the help under the label. A section's own switch with no help of its own
 * says, while off, that the rest of the section waits for it. */
function SwitchWidget({ id, value, label, disabled, readonly, onChange, schema }: WidgetProps) {
  const meta = schema as Meta
  return (
    <div className="flex items-start gap-2 py-1">
      <Switch id={id} checked={Boolean(value)} disabled={disabled || readonly} onCheckedChange={(checked) => onChange(checked)} />
      <div className="flex flex-col gap-1.5">
        <div className="flex min-h-4.5 items-center gap-1.5">
          <Label htmlFor={id}>{label}</Label>
          <InfoTip text={schema.description} label={label} />
        </div>
        <Help text={meta.help ?? (meta.section_switch && !value ? WAITING : undefined)} />
      </div>
    </div>
  )
}

/** Label and (i), the control, its help, then what a refused save said. Fields under a section switch
 * that is off look and work as usual; the switch's help says what it does, or else that they wait. */
function FieldTemplate(props: FieldTemplateProps) {
  const { id, children, displayLabel, rawErrors = [], errors, rawDescription, label, hidden, schema, uiSchema } = props
  // A section's or a list's adornment goes on its heading, a field's beside its control.
  const adornment = useContext(Adornments)[id]
  if (hidden) return <div className="hidden">{children}</div>
  // An entry of a plain list (arguments, domains) is its value alone; the list carries the label.
  const inList = typeof props.fieldPathId?.path.at(-1) === 'number'
  const ownLabel = inList || schema.type === 'boolean' || uiSchema?.['ui:widget'] === 'checkbox'
  // A switch draws its help under its label; a section or a map under its heading; the names field
  // under its own label, above the words it adds by itself.
  const ownHelp = ownLabel || schema.type === 'object' || (schema as Meta).widget === 'hint_words'
  return (
    <div className="flex flex-col gap-2">
      {displayLabel && !ownLabel ? (
        <div className="flex items-center gap-1.5">
          <label htmlFor={id} className={cn('text-sm leading-none font-medium', rawErrors.length > 0 && 'text-destructive')}>
            {label}
          </label>
          <InfoTip text={rawDescription} label={label} />
        </div>
      ) : null}
      {adornment !== undefined && !schema.properties && schema.type !== 'array' ? (
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">{children}</div>
          {adornment}
        </div>
      ) : (
        children
      )}
      {ownHelp ? null : <Help text={(schema as Meta).help} />}
      {errors}
    </div>
  )
}

/** A section heading inside a form, with its explanation behind an (i), the page's adornment at the
 * right, its lead line under it and, after a refused save, what is wrong with the section as a whole. */
function TitleField({ id, title, schema, registry }: TitleFieldProps) {
  const context = registry.formContext as FormContext | undefined
  const section = id.replace(/__title$/, '')
  const problems = context?.titleErrors?.[section] ?? []
  const adornment = useContext(Adornments)[section]
  return (
    <div id={id} className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <h3 className={cn('text-sm font-semibold', problems.length > 0 && 'text-destructive')}>{title}</h3>
        <InfoTip text={schema.description} label={title} />
        {adornment !== undefined ? (
          <>
            <span className="flex-1" />
            {adornment}
          </>
        ) : null}
      </div>
      <Help text={(schema as Meta).help} />
      {problems.map((p) => (
        <span key={p} className="text-sm text-destructive">
          {p}
        </span>
      ))}
    </div>
  )
}

function NoDescription() {
  return null
}

/** A quiet heading inside a card, over a group of fields. */
function GroupHeading({ children }: { children: ReactNode }) {
  return <h3 className="mt-2 text-sm font-semibold">{children}</h3>
}

/** Fields in schema order, except that fields sharing a `group` follow one heading, after the fields
 * that have none (so a field outside the group never reads as part of it). */
function grouped(items: ObjectFieldTemplatePropertyType[], groupOf: (name: string) => string | undefined): ReactNode[] {
  const groups = [...new Set(items.map((p) => groupOf(p.name)).filter((g) => g !== undefined))]
  return [
    ...items.filter((p) => groupOf(p.name) === undefined).map((p) => p.content),
    ...groups.map((g) => (
      <Fragment key={`group:${g}`}>
        <GroupHeading>{g}</GroupHeading>
        {items.filter((p) => groupOf(p.name) === g).map((p) => p.content)}
      </Fragment>
    )),
  ]
}

/** What a SchemaForm draws after its own fields: rows that are not config fields (`end`). Every
 * SchemaForm sets its own, so a form in a dialog inside a form starts afresh. */
const FormExtras = createContext<{ end?: ReactNode }>({})

/** A section's fields in schema order, groups after the ungrouped ones, then at the form's root its
 * own rows (`end`). */
function ObjectFieldTemplate({ title, properties, schema, fieldPathId, registry, uiSchema }: ObjectFieldTemplateProps) {
  const extras = useContext(FormExtras)
  const groupOf = (name: string) => ((schema.properties?.[name] ?? {}) as Meta).group
  const root = fieldPathId.path.length === 0
  return (
    <>
      {title ? <TitleField id={titleId(fieldPathId)} title={title} schema={schema} uiSchema={uiSchema} registry={registry} /> : null}
      <div className="flex flex-col gap-4">
        {grouped(properties, groupOf)}
        {root ? extras.end : null}
      </div>
    </>
  )
}

/** A plain list (domains): its label and (i) with Add, then one box per entry with remove. */
function ArrayFieldTemplate({ fieldPathId, title, schema, items, canAdd, onAddClick, disabled, readonly }: ArrayFieldTemplateProps) {
  const adornment = useContext(Adornments)[fieldPathId.$id]
  return (
    <div id={fieldPathId.$id} className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <span className="text-sm leading-none font-medium">{title}</span>
        <InfoTip text={schema.description} label={title} />
        <span className="flex-1" />
        {adornment}
        {canAdd ? (
          <Button type="button" variant="outline" size="sm" aria-label={`Add to ${title}`} disabled={disabled || readonly} onClick={onAddClick}>
            <Plus />
            Add
          </Button>
        ) : null}
      </div>
      {items.length > 0 ? <div className="flex flex-col gap-1.5">{items}</div> : null}
    </div>
  )
}

/** An entry of a plain list and its remove button. No list drawn this way has an order (aliases,
 * topics); an ordered one (a command line) is a text area instead. */
function ArrayFieldItemTemplate({ children, buttonsProps, disabled, readonly }: ArrayFieldItemTemplateProps) {
  const { hasRemove, onRemoveItem } = buttonsProps
  return (
    <div className="flex items-center gap-1">
      <div className="min-w-0 flex-1">{children}</div>
      <IconButton label="Remove" type="button" disabled={disabled || readonly || !hasRemove} onClick={onRemoveItem}>
        <Trash2 />
      </IconButton>
    </div>
  )
}

const templates = {
  BaseInputTemplate: PlainInput,
  FieldTemplate,
  ObjectFieldTemplate,
  TitleFieldTemplate: TitleField,
  DescriptionFieldTemplate: NoDescription,
  ArrayFieldTemplate,
  ArrayFieldItemTemplate,
}

/** A plain text or number box, the app's own input like every other field (also rjsf's number box).
 * Inside an InputGroup it takes the group's borderless input. */
function PlainInput({ id, value, disabled, readonly, autofocus, placeholder, onChange, onBlur, onFocus, schema, options, type, inGroup }: BaseInputTemplateProps & { inGroup?: boolean }) {
  const inputProps = getInputProps(schema, type, options)
  const Box = inGroup ? InputGroupInput : Input
  return (
    <Box
      id={id}
      {...inputProps}
      value={value ?? ''}
      placeholder={placeholder || (schema as Meta).placeholder}
      disabled={disabled || readonly}
      autoFocus={autofocus}
      onChange={(e) => onChange(e.target.value === '' ? options.emptyValue : e.target.value)}
      onBlur={(e) => onBlur(id, e.target.value)}
      onFocus={(e) => onFocus(id, e.target.value)}
    />
  )
}

/** A string field whose schema names a live list: a searchable dropdown that also takes a typed value. */
function OptionsWidget({ id, value, disabled, readonly, onChange, schema }: WidgetProps) {
  const meta = schema as Meta
  const { choices, loading, problem } = useOptions(meta.options ?? '', meta.options_query)
  // A list that could not be fetched (no token yet, server down) leaves a text box and says why,
  // unless its section is off and nothing is expected to work yet.
  if (problem && !loading && choices.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <Input
          id={id}
          value={value ?? ''}
          placeholder={meta.placeholder}
          disabled={disabled || readonly}
          onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)}
        />
        {meta.waits ? null : <Help text={problem} />}
      </div>
    )
  }
  return (
    <Combobox
      id={id}
      value={value ?? ''}
      choices={choices}
      custom
      loading={loading}
      problem={problem}
      disabled={disabled || readonly}
      onChange={(v) => onChange(v)}
      onClear={schema.default === null ? () => onChange(undefined) : undefined}
      placeholder={meta.placeholder}
    />
  )
}

/** An enum as a dropdown with the schema's labels and icons. A dropdown item cannot hold '', so a
 * choice whose value is empty ("The box's setting") stands in as EMPTY. */
const NONE = '__none__'
const EMPTY = '__empty__'

/** An enum as a dropdown. An optional one (its default is null) has a first item for none, named by
 * its placeholder, so a choice can be taken back. */
function EnumWidget({ id, value, disabled, readonly, onChange, schema }: WidgetProps) {
  const choices = enumChoices(schema as Meta)
  const optional = schema.default === null
  return (
    <Select
      value={value === '' ? EMPTY : (value ?? (optional ? NONE : ''))}
      disabled={disabled || readonly || choices.length + (optional ? 1 : 0) < 2}
      onValueChange={(v) => onChange(v === NONE ? undefined : v === EMPTY ? '' : v)}
    >
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {optional && <SelectItem value={NONE}>{(schema as Meta).placeholder ?? 'None'}</SelectItem>}
        {choices.map((c) => (
          <SelectItem key={c.value} value={c.value === '' ? EMPTY : c.value}>
            <KindIcon name={c.icon} />
            {c.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function step(schema: JsonSchema): number {
  if (schema.type === 'integer') return 1
  const span = Number(schema.maximum) - Number(schema.minimum)
  return span <= 1 ? 0.01 : span <= 5 ? 0.05 : 0.5
}

/** A number as words where its schema names it ("Off"), else with its unit ("0.5 s"). */
function shown(value: number, schema: Meta): string {
  const n = Number(value.toFixed(2))
  return schema.labels?.[String(n)] ?? (schema.unit ? `${n} ${schema.unit}` : String(n))
}

/** A number with both bounds that asks for a slider (`widget: slider`), its value beside it. */
function RangeWidget({ id, value, label, disabled, readonly, onChange, schema }: WidgetProps) {
  const min = Number(schema.minimum)
  const max = Number(schema.maximum)
  const s = step(schema as JsonSchema)
  const current = typeof value === 'number' ? value : Number(schema.default ?? min)
  const digits = s < 1 ? 2 : 0
  return (
    <div className="flex items-center gap-4 py-2">
      <Slider
        id={id}
        min={min}
        max={max}
        step={s}
        value={[current]}
        disabled={disabled || readonly}
        onValueChange={([v]) => onChange(Number(v.toFixed(digits)))}
        thumbProps={{ 'aria-label': label, 'aria-valuetext': shown(current, schema as Meta) }}
      />
      <span className="min-w-12 text-right text-sm whitespace-nowrap tabular-nums">{shown(current, schema as Meta)}</span>
    </div>
  )
}

/** Every other number: a box with its unit after the value, and the word for a special value beside it
 * ("Off" for 0). */
function NumberWidget(props: WidgetProps) {
  const meta = props.schema as Meta
  const word = meta.labels?.[String(props.value)]
  return (
    <div className="flex items-center gap-2">
      <InputGroup className="w-40">
        <PlainInput {...props} inGroup />
        {meta.unit ? <InputGroupAddon align="inline-end">{meta.unit}</InputGroupAddon> : null}
      </InputGroup>
      {word ? <span className="text-sm text-muted-foreground">{word}</span> : null}
    </div>
  )
}

/** "HH:MM-HH:MM" as a switch and two time fields; off stores '' (never quiet), not the default. */
function TimeRangeWidget({ id, value, label, disabled, readonly, onChange }: WidgetProps) {
  const [from = '22:00', to = '07:00'] = typeof value === 'string' && value.includes('-') ? value.split('-') : []
  const on = typeof value === 'string' && value !== ''
  return (
    <div className="flex flex-wrap items-center gap-3 py-1">
      <Switch
        id={id}
        checked={on}
        disabled={disabled || readonly}
        onCheckedChange={(checked) => onChange(checked ? `${from}-${to}` : '')}
      />
      {on ? (
        <>
          <Input type="time" aria-label={`${label}, from`} className="w-32" value={from} onChange={(e) => onChange(`${e.target.value}-${to}`)} />
          <span className="flex items-center gap-3">
            <span className="text-muted-foreground">to</span>
            <Input type="time" aria-label={`${label}, until`} className="w-32" value={to} onChange={(e) => onChange(`${from}-${e.target.value}`)} />
          </span>
        </>
      ) : null}
    </div>
  )
}

/** The value field prune puts beside a secret's name field: the key goes to the secrets draft, never
 * into the config; pasting one while the name field is empty also writes the default name. */
function SecretWidget({ id, disabled, readonly, schema, registry }: WidgetProps) {
  const meta = (schema as Meta).secret_field
  const context = registry.formContext as FormContext | undefined
  if (!meta) return null
  return (
    <SecretField
      id={id}
      names={[meta.name]}
      label={schema.title ?? 'Key'}
      disabled={disabled || readonly}
      optional={meta.optional}
      quiet={meta.quiet}
      onPaste={(name) => {
        if (meta.unnamed) context?.setAt?.(meta.path, name)
      }}
    />
  )
}

/** An extension's `Account` setting: Connect, or Connected with Disconnect. */
function AccountWidget({ id, disabled, readonly, schema }: WidgetProps) {
  const meta = (schema as Meta).account_field
  return meta ? <AccountField id={id} {...meta} disabled={disabled || readonly} /> : null
}

/** Every text and number field comes through here and is routed by what its schema says. */
function TextWidget(props: WidgetProps) {
  const schema = props.schema as Meta
  if (schema.widget === 'secret') return <SecretWidget {...props} />
  if (schema.widget === 'account') return <AccountWidget {...props} />
  if (schema.options) return <OptionsWidget {...props} />
  if (schema.widget === 'time_range') return <TimeRangeWidget {...props} />
  if (schema.widget === 'time')
    return (
      <div className="w-32">
        <PlainInput {...props} type="time" />
      </div>
    )
  if (schema.const !== undefined) return <EnumWidget {...props} />
  if (schema.type === 'number' || schema.type === 'integer') {
    const bounded = schema.minimum !== undefined && schema.maximum !== undefined
    return schema.widget === 'slider' && bounded ? <RangeWidget {...props} /> : <NumberWidget {...props} />
  }
  return <PlainInput {...props} />
}

function SelectWidget(props: WidgetProps) {
  if (props.multiple) return <CheckboxesWidget {...props} />
  return <EnumWidget {...props} />
}

const widgets = { CheckboxWidget: SwitchWidget, TextWidget, SelectWidget }

const DefaultObjectField = getDefaultRegistry().fields.ObjectField
const DefaultArrayField = getDefaultRegistry().fields.ArrayField

/** Objects of entries (contacts, environment, volumes) are tables with a dialog; others as usual. */
function ObjectField(props: FieldProps) {
  if (isKeyedMap(props.schema as JsonSchema)) return <KeyedMapField {...props} />
  return <DefaultObjectField {...props} />
}

/** One argument per line: trimmed, empty lines dropped. */
function argLines(text: string): string[] {
  return text.split('\n').map((l) => l.trim()).filter(Boolean)
}

/** A command line (engine arguments, a start command) as one text area, one argument per line. A flag
 * given twice gets a line saying so. */
function ArgsWidget({ schema, formData, onChange, fieldPathId, disabled, readonly }: FieldProps) {
  const args = Array.isArray(formData) ? formData.map(String) : []
  // The typed text, with its trailing newline or space, while it still reads as the saved list; a
  // change from elsewhere (Discard, a preset) shows the list itself.
  const [text, setText] = useState(args.join('\n'))
  const value = deepEqual(argLines(text), args) ? text : args.join('\n')
  const counts = new Map<string, number>()
  for (const a of args.filter((a) => /^--?[a-zA-Z]/.test(a))) {
    const flag = a.split('=')[0]
    counts.set(flag, (counts.get(flag) ?? 0) + 1)
  }
  const id = fieldPathId.$id
  const adornment = useContext(Adornments)[id]
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <label htmlFor={id} className="text-sm leading-none font-medium">
          {schema.title}
        </label>
        <InfoTip text={schema.description} label={schema.title} />
        <span className="flex-1" />
        {adornment}
      </div>
      <Textarea
        id={id}
        value={value}
        spellCheck={false}
        disabled={disabled || readonly}
        onChange={(e) => {
          setText(e.target.value)
          onChange(argLines(e.target.value), fieldPathId.path)
        }}
      />
      {[...counts].filter(([, n]) => n > 1).map(([flag, n]) => (
        <p key={flag} className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <CircleAlert className="size-4 text-warning" />
          {`${flag} appears ${n === 2 ? 'twice' : `${n} times`}.`}
        </p>
      ))}
    </div>
  )
}

/** A list whose schema names its own widget (hint words, a command line) draws that; others are one
 * box per entry. */
function ArrayField(props: FieldProps) {
  const widget = (props.schema as Meta).widget
  if (widget === 'hint_words') return <HintWordsField {...props} />
  if (widget === 'args') return <ArgsWidget {...props} />
  return <DefaultArrayField {...props} />
}

const fields = { ObjectField, ArrayField }

type Props = {
  schema: JsonSchema
  value: unknown
  onChange: (next: unknown) => void
  /** Prefix for field ids; a SectionForm passes fieldId(its path) so ids match config paths. */
  idPrefix?: string
  /** Errors from a refused save, nested like the data (rjsf's ErrorSchema). */
  extraErrors?: ErrorSchema
  formContext?: FormContext
  /** Rows after the fields that are not config fields; see FormExtras. */
  end?: ReactNode
}

/** A form generated from a (pruned) JSON Schema; the page owns the data and the save button. It draws
 * a div, not a form: nothing submits it, so a button in its rows never runs its validation. */
export function SchemaForm({ schema, value, onChange, idPrefix, extraErrors, formContext, end }: Props) {
  return (
    <FormExtras.Provider value={{ end }}>
      <Form
        tagName="div"
        schema={schema as RJSFSchema}
        uiSchema={uiSchema}
        formData={value}
        validator={validator}
        widgets={widgets}
        fields={fields}
        templates={templates}
        idPrefix={idPrefix}
        extraErrors={extraErrors}
        formContext={formContext}
        onChange={(event) => onChange(event.formData)}
        liveValidate={false}
        showErrorList={false}
        noHtml5Validate
      />
    </FormExtras.Provider>
  )
}
