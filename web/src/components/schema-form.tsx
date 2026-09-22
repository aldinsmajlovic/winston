import Form from '@rjsf/shadcn'
import validator from '@rjsf/validator-ajv8'
import type { RJSFSchema, UiSchema, WidgetProps } from '@rjsf/utils'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { JsonSchema } from '@/lib/schema'

const uiSchema: UiSchema = { 'ui:submitButtonOptions': { norender: true } }

function SwitchWidget({ id, value, label, disabled, readonly, onChange }: WidgetProps) {
  return (
    <div className="flex items-center gap-2 py-1">
      <Switch
        id={id}
        checked={Boolean(value)}
        disabled={disabled || readonly}
        onCheckedChange={(checked) => onChange(checked)}
      />
      <Label htmlFor={id}>{label}</Label>
    </div>
  )
}

const widgets = { CheckboxWidget: SwitchWidget }

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
      onChange={(event) => onChange(event.formData)}
      liveValidate={false}
      showErrorList={false}
      noHtml5Validate
    />
  )
}
