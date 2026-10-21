import { useCallback, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react'
import { fieldId, useSectionErrors } from '@/api/field-errors'
import { KindIcon } from '@/components/kind-icon'
import { SchemaForm, type FormContext } from '@/components/schema-form'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { SettingsState } from '@/hooks/use-settings'
import { Adornments } from '@/lib/adornments'
import { applyForm, getIn, prune, section, setIn, type JsonSchema, type Path } from '@/lib/schema'

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
  /** Rows that are not config fields (a connection check), after the fields. */
  children?: ReactNode
  /** Controls beside a field or on a nested section's heading (a play button), by the dotted path
   * from this section: `{ voice: <Play/> }`. Shown only while the form shows that field. */
  adornments?: Record<string, ReactNode>
}

/** One section of the config as a form, its `help` as the card's lead line and its own rows inside the form. What the form shows follows the draft: fields
 * whose `when` fails are left out, a switched-off feature's switch with no help says the rest of its
 * section waits, and errors from a refused save sit beside their fields. */
export function SectionForm({ page, path, title, omit, driver, icon: iconName, status, children, adornments }: Props) {
  const { schema, value } = page
  // Pruned on every change of the draft, but handed to rjsf as the same object while it reads the same.
  const pruned = JSON.stringify(prune(section(schema, path), schema, { omit, driver, config: value, path }))
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
  // rjsf skips redrawing a field whose props are deep-equal and counts every function as equal, so a
  // field can hold an old setAt; it reaches the current page through the ref.
  const latest = useRef(page)
  useLayoutEffect(() => {
    latest.current = page
  })
  const context = useMemo<FormContext>(
    () => ({
      setAt: (at, next) => latest.current.update((c) => setIn(c, at, next)),
      config: value,
      titleErrors: errors.byTitle,
    }),
    [value, errors.byTitle],
  )
  const byId = Object.fromEntries(Object.entries(adornments ?? {}).map(([key, node]) => [fieldId([...path, ...key.split('.')]), node]))
  const own = errors.own.map((msg) => (
    <span key={msg} className="text-sm font-normal text-destructive">
      {msg}
    </span>
  ))
  const form = (
    <Adornments.Provider value={byId}>
      <SchemaForm
        schema={formSchema}
        value={getIn(value, path)}
        idPrefix={fieldId(path)}
        extraErrors={errors.extraErrors}
        formContext={context}
        end={children}
        onChange={(next) =>
          page.update((c) => {
            const before = getIn(c, path)
            const after = applyForm(before, next, formSchema)
            return after === before ? c : setIn(c, path, after)
          })
        }
      />
    </Adornments.Provider>
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
        {typeof formSchema.help === 'string' ? <CardDescription>{formSchema.help}</CardDescription> : null}
      </CardHeader>
      <CardContent>
        {form}
      </CardContent>
    </Card>
  )
}
