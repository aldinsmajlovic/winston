import Form, { Templates, Widgets } from '@rjsf/shadcn'
import validator from '@rjsf/validator-ajv8'
import type { FieldTemplateProps, RJSFSchema, TitleFieldProps, UiSchema, WidgetProps } from '@rjsf/utils'
import { Combobox, type Choice } from '@/components/combobox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { InfoTip } from '@/components/info-tip'
import { KindIcon } from '@/components/kind-icon'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { useOptions } from '@/hooks/use-options'
import type { JsonSchema } from '@/lib/schema'

// The theme's own input and checkbox list, for fields none of the widgets below take over.
const BaseInputTemplate = Templates.BaseInputTemplate!
const CheckboxesWidget = Widgets.CheckboxesWidget

const uiSchema: UiSchema = { 'ui:submitButtonOptions': { norender: true } }

type Meta = JsonSchema & {
  options?: string
  options_query?: { domain?: string }
  labels?: Record<string, string>
  icons?: Record<string, string>
  widget?: string
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

const WrapIfAdditional = Templates.WrapIfAdditionalTemplate!

/** Label, then the control; the field's explanation sits behind an (i) beside the label. */
function FieldTemplate(props: FieldTemplateProps) {
  const { id, children, displayLabel, rawErrors = [], errors, help, rawDescription, label, hidden, required, schema, uiSchema } = props
  if (hidden) return <div className="hidden">{children}</div>
  const ownLabel = schema.type === 'boolean' || uiSchema?.['ui:widget'] === 'checkbox'
  return (
    <WrapIfAdditional {...props}>
      <div className="flex flex-col gap-2">
        {displayLabel && !ownLabel ? (
          <div className="flex items-center gap-1.5">
            <label htmlFor={id} className={cn('text-sm leading-none font-medium', rawErrors.length > 0 && 'text-destructive')}>
              {label}
              {required ? '*' : null}
            </label>
            <InfoTip text={rawDescription} />
          </div>
        ) : null}
        {children}
        {errors}
        {help}
      </div>
    </WrapIfAdditional>
  )
}

/** A section heading inside a form, with its explanation behind an (i). */
function TitleField({ id, title, schema }: TitleFieldProps) {
  return (
    <div id={id} className="my-1 flex flex-col gap-0.5">
      <div className="flex items-center gap-1.5">
        <h5>{title}</h5>
        <InfoTip text={schema.description} />
      </div>
      <Separator className="my-1" />
    </div>
  )
}

function NoDescription() {
  return null
}

const templates = { FieldTemplate, TitleFieldTemplate: TitleField, DescriptionFieldTemplate: NoDescription }

/** A string field whose schema names a live list: a searchable dropdown that also takes a typed value. */
function OptionsWidget({ id, value, disabled, readonly, onChange, schema }: WidgetProps) {
  const meta = schema as Meta
  const { choices, loading, problem } = useOptions(meta.options ?? '', meta.options_query)
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
    />
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

/** Every text and number field comes through here and is routed by what its schema says. */
function TextWidget(props: WidgetProps) {
  const schema = props.schema as Meta
  if (schema.options) return <OptionsWidget {...props} />
  if (schema.widget === 'time_range') return <TimeRangeWidget {...props} />
  if (schema.const !== undefined) return <EnumWidget {...props} />
  const numeric = schema.type === 'number' || schema.type === 'integer'
  if (numeric && schema.minimum !== undefined && schema.maximum !== undefined) return <RangeWidget {...props} />
  return <BaseInputTemplate {...props} />
}

function SelectWidget(props: WidgetProps) {
  if (props.multiple) return <CheckboxesWidget {...props} />
  return <EnumWidget {...props} />
}

const widgets = { CheckboxWidget: SwitchWidget, TextWidget, SelectWidget }

type Props = {
  schema: JsonSchema
  value: unknown
  onChange: (next: unknown) => void
}

/** A form generated from a (pruned) JSON Schema; the page owns the data and the save button. */
export function SchemaForm({ schema, value, onChange }: Props) {
  return (
    <Form
      schema={schema as RJSFSchema}
      uiSchema={uiSchema}
      formData={value}
      validator={validator}
      widgets={widgets}
      templates={templates}
      onChange={(event) => onChange(event.formData)}
      liveValidate={false}
      showErrorList={false}
      noHtml5Validate
    />
  )
}
