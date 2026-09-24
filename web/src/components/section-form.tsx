import { useCallback, useMemo, type ReactNode } from 'react'
import { fieldId, useSectionErrors } from '@/api/field-errors'
import { KindIcon } from '@/components/kind-icon'
import { SchemaForm, type FormContext } from '@/components/schema-form'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { SettingsState } from '@/hooks/use-settings'
import { applyForm, EXTRA_SUFFIX, getIn, prune, section, setIn, type JsonSchema, type Path } from '@/lib/schema'

/** A row a page adds to a section's form that is not a config field (what Ask Claude signs in with,
 * its key): drawn with the same label and (i) as the fields, after the field `after`. */
export type ExtraRow = {
  key: string
  title: string
  description?: string
  after?: string
  render: (id: string) => ReactNode
}

type Props = {
  page: SettingsState
  path: string[]
  title?: string
  omit?: string[]
  driver?: string
  /** A logo for the card title instead of the schema's `icon`. */
  icon?: string
  /** Beside the card title, e.g. the channel's state icon. */
  status?: ReactNode
  extras?: ExtraRow[]
}

/** Put the page's extra rows into the pruned schema as virtual fields (see ExtraWidget). */
function withExtras(schema: JsonSchema, extras: Omit<ExtraRow, 'render'>[]): JsonSchema {
  if (!extras.length || !schema.properties) return schema
  const dimmed = Object.values(schema.properties).some((p) => p.dimmed === true)
  const rows = new Map(
    extras.map((x) => [
      x,
      {
        type: 'string',
        title: x.title,
        description: x.description,
        widget: 'extra',
        extra: x.key,
        virtual: true,
        ...(dimmed ? { dimmed: true } : {}),
      } as JsonSchema,
    ]),
  )
  const properties: Record<string, JsonSchema> = {}
  const placed = new Set<Omit<ExtraRow, 'render'>>()
  // A row goes after the field or the other row it names; one whose field is not shown goes last.
  const follow = (key: string) => {
    for (const x of extras) {
      if (x.after === key && !placed.has(x)) {
        properties[x.key + EXTRA_SUFFIX] = rows.get(x)!
        placed.add(x)
        follow(x.key)
      }
    }
  }
  for (const [key, field] of Object.entries(schema.properties)) {
    properties[key] = field
    follow(key)
  }
  for (const x of extras) if (!placed.has(x)) properties[x.key + EXTRA_SUFFIX] = rows.get(x)!
  return { ...schema, properties }
}

/** One section of the config as a form, at the page's current level (basic or advanced). What the form
 * shows follows the draft: fields whose `when` fails are left out, a switched-off feature greys the
 * rest of its section, and errors from a refused save sit beside their fields. */
export function SectionForm({ page, path, title, omit, driver, icon: iconName, status, extras }: Props) {
  const { schema, advanced, value } = page
  // Pruned on every change of the draft, but handed to rjsf as the same object while it reads the same.
  const extraSpecs = (extras ?? []).map(({ key, title, description, after }) => ({ key, title, description, after }))
  const pruned = JSON.stringify(
    withExtras(prune(section(schema, path), schema, { advanced, omit, driver, config: value, path }), extraSpecs),
  )
  const formSchema = useMemo(() => JSON.parse(pruned) as JsonSchema, [pruned])
  const isObject = useCallback(
    (rel: Path) => {
      try {
        const s = section(schema, [...path, ...rel.map(String)])
        return Boolean(s.properties)
      } catch {
        return false
      }
    },
    [schema, path],
  )
  const errors = useSectionErrors(path, isObject)
  const context = useMemo<FormContext>(
    () => ({
      setAt: (at, next) => page.update((c) => setIn(c, at, next)),
      titleErrors: errors.byTitle,
      extras: Object.fromEntries((extras ?? []).map((x) => [x.key, x.render])),
    }),
    [page, errors.byTitle, extras],
  )
  const own = errors.own.map((msg) => (
    <span key={msg} className="text-sm font-normal text-destructive">
      {msg}
    </span>
  ))
  const form = (
    <SchemaForm
      schema={formSchema}
      value={getIn(value, path)}
      idPrefix={fieldId(path)}
      extraErrors={errors.extraErrors}
      formContext={context}
      onChange={(next) => page.update((c) => setIn(c, path, applyForm(getIn(c, path), next, formSchema)))}
    />
  )
  const icon = iconName ?? (typeof formSchema.icon === 'string' ? formSchema.icon : undefined)
  if (!title) {
    return (
      <div id={fieldId(path)} className="flex flex-col gap-2">
        {own}
        {form}
      </div>
    )
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle id={fieldId(path)} className="flex flex-wrap items-center gap-2">
          <KindIcon name={icon} />
          {title}
          {status}
          {own}
        </CardTitle>
      </CardHeader>
      <CardContent>{form}</CardContent>
    </Card>
  )
}
