import { useContext, type ReactNode } from 'react'
import { getDefaultRegistry } from '@rjsf/core'
import Form, { Widgets } from '@rjsf/shadcn'
import validator from '@rjsf/validator-ajv8'
import {
  getInputProps,
  type ArrayFieldItemTemplateProps,
  type ArrayFieldTemplateProps,
  type BaseInputTemplateProps,
  type ErrorSchema,
  type FieldProps,
  type FieldTemplateProps,
  type RJSFSchema,
  type TitleFieldProps,
  type UiSchema,
  type WidgetProps,
} from '@rjsf/utils'
import { ChevronDown, ChevronUp, CircleAlert, Plus, Trash2 } from 'lucide-react'
import { Combobox, type Choice } from '@/components/combobox'
import { IconButton } from '@/components/icon-button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { InfoTip } from '@/components/info-tip'
import { KindIcon } from '@/components/kind-icon'
import { HintWordsField } from '@/components/hint-words-field'
import { KeyedMapField } from '@/components/keyed-map-field'
import { SecretField } from '@/components/secret-field'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Adornments } from '@/lib/adornments'
import { cn } from '@/lib/utils'
import { useOptions } from '@/hooks/use-options'
import { isKeyedMap, type JsonSchema, type Path, type SecretField as SecretMeta } from '@/lib/schema'

// The theme's own checkbox list, for multiple choice fields.
const CheckboxesWidget = Widgets.CheckboxesWidget

const uiSchema: UiSchema = { 'ui:submitButtonOptions': { norender: true } }

/** What a SectionForm hands its fields beyond their own value. */
export type FormContext = {
  /** Set a value anywhere in the config draft (a pasted key writes its variable name). */
  setAt?: (path: Path, value: unknown) => void
  /** Errors about a nested object, by the id of its title, shown beside the title. */
  titleErrors?: Record<string, string[]>
  /** Rows the page draws inside the form (SectionForm `extras`), by key. */
  extras?: Record<string, (id: string) => ReactNode>
}

type Meta = JsonSchema & {
  options?: string
  options_query?: { domain?: string }
  labels?: Record<string, string>
  icons?: Record<string, string>
  widget?: string
  dimmed?: boolean
  secret_field?: SecretMeta
  extra?: string
  placeholder?: string // what an empty picker means
}

/** "reasoning_effort" -> "Reasoning effort", for enum values the schema gives no label. */
function humanize(value: string): string {
  const s = value.replaceAll('_', ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function enumChoices(schema: Meta): Choice[] {
  const values = (schema.enum ?? (schema.const !== undefined ? [schema.const] : [])) as string[]
  return values.map((v) => ({ value: v, label: schema.labels?.[v] ?? humanize(String(v)), icon: schema.icons?.[v] }))
}

function SwitchWidget({ id, value, label, disabled, readonly, onChange, schema }: WidgetProps) {
  return (
    <div className="flex items-center gap-2 py-1">
      <Switch id={id} checked={Boolean(value)} disabled={disabled || readonly} onCheckedChange={(checked) => onChange(checked)} />
      <Label htmlFor={id}>{label}</Label>
      <InfoTip text={schema.description} />
    </div>
  )
}

/** Label, then the control; the field's explanation sits behind an (i) beside the label. A field under
 * a feature switch that is off (`dimmed` from prune) is greyed but still editable. */
function FieldTemplate(props: FieldTemplateProps) {
  const { id, children, displayLabel, rawErrors = [], errors, help, rawDescription, label, hidden, required, schema, uiSchema } = props
  // A section's adornment goes on its heading (TitleField), a field's beside its control.
  const adornment = useContext(Adornments)[id]
  if (hidden) return <div className="hidden">{children}</div>
  // An entry of a plain list (arguments, domains) is its value alone; the list carries the label.
  const inList = typeof props.fieldPathId?.path.at(-1) === 'number'
  const ownLabel = inList || schema.type === 'boolean' || uiSchema?.['ui:widget'] === 'checkbox'
  return (
    <div className={cn('flex flex-col gap-2', (schema as Meta).dimmed === true && 'opacity-50')}>
      {displayLabel && !ownLabel ? (
        <div className="flex items-center gap-1.5">
          <label htmlFor={id} className={cn('text-sm leading-none font-medium', rawErrors.length > 0 && 'text-destructive')}>
            {label}
            {required ? '*' : null}
          </label>
          <InfoTip text={rawDescription} />
        </div>
      ) : null}
      {adornment !== undefined && !schema.properties ? (
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">{children}</div>
          {adornment}
        </div>
      ) : (
        children
      )}
      {errors}
      {help}
    </div>
  )
}

/** A section heading inside a form, with its explanation behind an (i), the page's adornment at the
 * right and, after a refused save, what is wrong with the section as a whole. */
function TitleField({ id, title, schema, registry }: TitleFieldProps) {
  const context = registry.formContext as FormContext | undefined
  const section = id.replace(/__title$/, '')
  const problems = context?.titleErrors?.[section] ?? []
  const adornment = useContext(Adornments)[section]
  return (
    <div id={id} className="my-1 flex flex-col gap-0.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <h5 className={cn(problems.length > 0 && 'text-destructive')}>{title}</h5>
        <InfoTip text={schema.description} />
        {adornment !== undefined ? (
          <>
            <span className="flex-1" />
            {adornment}
          </>
        ) : null}
      </div>
      {problems.map((p) => (
        <span key={p} className="text-sm text-destructive">
          {p}
        </span>
      ))}
      <Separator className="my-1" />
    </div>
  )
}

function NoDescription() {
  return null
}

/** A plain list (arguments, domains): its label and (i) with an Add icon, then one box per entry with
 * move and remove icons on hover. */
function ArrayFieldTemplate({ fieldPathId, title, schema, items, canAdd, onAddClick, disabled, readonly }: ArrayFieldTemplateProps) {
  return (
    <div id={fieldPathId.$id} className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <span className="text-sm leading-none font-medium">{title}</span>
        <InfoTip text={schema.description} />
        <span className="flex-1" />
        {canAdd ? (
          <IconButton label="Add" type="button" variant="outline" disabled={disabled || readonly} onClick={onAddClick}>
            <Plus />
          </IconButton>
        ) : null}
      </div>
      {items.length > 0 ? <div className="flex flex-col gap-1.5">{items}</div> : null}
    </div>
  )
}

function ArrayFieldItemTemplate({ children, buttonsProps, disabled, readonly }: ArrayFieldItemTemplateProps) {
  const { hasMoveUp, hasMoveDown, hasRemove, onMoveUpItem, onMoveDownItem, onRemoveItem } = buttonsProps
  const locked = disabled || readonly
  return (
    <div className="group flex items-center gap-1">
      <div className="flex-1">{children}</div>
      <div className="flex opacity-0 group-focus-within:opacity-100 group-hover:opacity-100">
        <IconButton label="Move up" type="button" disabled={locked || !hasMoveUp} onClick={onMoveUpItem}>
          <ChevronUp />
        </IconButton>
        <IconButton label="Move down" type="button" disabled={locked || !hasMoveDown} onClick={onMoveDownItem}>
          <ChevronDown />
        </IconButton>
      </div>
      <IconButton label="Remove" type="button" disabled={locked || !hasRemove} onClick={onRemoveItem}>
        <Trash2 />
      </IconButton>
    </div>
  )
}

const templates = {
  BaseInputTemplate: PlainInput,
  FieldTemplate,
  TitleFieldTemplate: TitleField,
  DescriptionFieldTemplate: NoDescription,
  ArrayFieldTemplate,
  ArrayFieldItemTemplate,
}

/** A plain text or number box, the app's own input like every other field (also rjsf's number box). */
function PlainInput({ id, value, disabled, readonly, autofocus, placeholder, onChange, onBlur, onFocus, schema, options, type }: BaseInputTemplateProps) {
  const inputProps = getInputProps(schema, type, options)
  return (
    <Input
      id={id}
      {...inputProps}
      value={value ?? ''}
      placeholder={placeholder}
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
  // A list that could not be fetched (no token yet, server down) leaves a text box and says why.
  if (problem && !loading && choices.length === 0) {
    return (
      <div className="flex items-center gap-2">
        <Input
          id={id}
          value={value ?? ''}
          disabled={disabled || readonly}
          onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)}
        />
        <ProblemIcon problem={problem} />
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

function ProblemIcon({ problem }: { problem: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">
          <CircleAlert className="size-4 text-warning" aria-label={problem} />
        </span>
      </TooltipTrigger>
      <TooltipContent>{problem}</TooltipContent>
    </Tooltip>
  )
}

/** An enum as a dropdown with the schema's labels and icons. */
function EnumWidget({ id, value, disabled, readonly, onChange, schema }: WidgetProps) {
  const choices = enumChoices(schema as Meta)
  return (
    <Select value={value ?? ''} disabled={disabled || readonly || choices.length < 2} onValueChange={(v) => onChange(v)}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {choices.map((c) => (
          <SelectItem key={c.value} value={c.value}>
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

/** A number with both bounds as a slider with its value beside it. */
function RangeWidget({ id, value, disabled, readonly, onChange, schema }: WidgetProps) {
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
      />
      <span className="w-12 text-right text-sm tabular-nums">{current.toFixed(digits)}</span>
    </div>
  )
}

/** "HH:MM-HH:MM" as a switch and two time fields; off stores '' (never quiet), not the default. */
function TimeRangeWidget({ id, value, disabled, readonly, onChange }: WidgetProps) {
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
          <Input type="time" aria-label="From" className="w-32" value={from} onChange={(e) => onChange(`${e.target.value}-${to}`)} />
          <span className="text-muted-foreground">to</span>
          <Input type="time" aria-label="To" className="w-32" value={to} onChange={(e) => onChange(`${from}-${e.target.value}`)} />
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
      onPaste={(name) => {
        if (meta.unnamed) context?.setAt?.(meta.path, name)
      }}
    />
  )
}

/** A row the page draws itself (SectionForm `extras`), under the form's label and (i). */
function ExtraWidget({ id, schema, registry }: WidgetProps) {
  const key = (schema as Meta).extra
  const render = key ? (registry.formContext as FormContext | undefined)?.extras?.[key] : undefined
  return render ? <>{render(id)}</> : null
}

/** Every text and number field comes through here and is routed by what its schema says. */
function TextWidget(props: WidgetProps) {
  const schema = props.schema as Meta
  if (schema.widget === 'extra') return <ExtraWidget {...props} />
  if (schema.widget === 'secret') return <SecretWidget {...props} />
  if (schema.options) return <OptionsWidget {...props} />
  if (schema.widget === 'time_range') return <TimeRangeWidget {...props} />
  if (schema.const !== undefined) return <EnumWidget {...props} />
  const numeric = schema.type === 'number' || schema.type === 'integer'
  if (numeric && schema.minimum !== undefined && schema.maximum !== undefined) return <RangeWidget {...props} />
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

/** A list whose schema names its own widget (hint words) draws that; others are one box per entry. */
function ArrayField(props: FieldProps) {
  if ((props.schema as Meta).widget === 'hint_words') return <HintWordsField {...props} />
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
}

/** A form generated from a (pruned) JSON Schema; the page owns the data and the save button. */
export function SchemaForm({ schema, value, onChange, idPrefix, extraErrors, formContext }: Props) {
  return (
    <Form
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
  )
}
