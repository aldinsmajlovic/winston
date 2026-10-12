import { useCallback, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react'
import { fieldId, useSectionErrors } from '@/api/field-errors'
import { KindIcon } from '@/components/kind-icon'
import { SchemaForm, type FormContext } from '@/components/schema-form'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
  /** Rows after the form that are not config fields (what Ask Claude signs in with, its key). */
  children?: ReactNode
  /** Controls beside a field or on a nested section's heading (a play button), by the dotted path
   * from this section: `{ voice: <Play/> }`. Shown only while the form shows that field. */
  adornments?: Record<string, ReactNode>
}

/** One section of the config as a form, at the page's current level (basic or advanced). What the form
 * shows follows the draft: fields whose `when` fails are left out, a switched-off feature greys the
 * rest of its section, and errors from a refused save sit beside their fields. */
export function SectionForm({ page, path, title, omit, driver, icon: iconName, status, children, adornments }: Props) {
  const { schema, advanced, value } = page
  // Pruned on every change of the draft, but handed to rjsf as the same object while it reads the same.
  const pruned = JSON.stringify(prune(section(schema, path), schema, { advanced, omit, driver, config: value, path }))
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
        {children}
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
      <CardContent>
        {form}
        {children}
      </CardContent>
    </Card>
  )
}
