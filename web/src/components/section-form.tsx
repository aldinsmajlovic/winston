import { useMemo } from 'react'
import { SchemaForm } from '@/components/schema-form'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { SettingsState } from '@/hooks/use-settings'
import { applyForm, getIn, prune, section, setIn } from '@/lib/schema'

type Props = {
  page: SettingsState
  path: string[]
  title?: string
  omit?: string[]
  driver?: string
}

/** One section of the config as a form, at the page's current level (basic or advanced). */
export function SectionForm({ page, path, title, omit, driver }: Props) {
  const { schema, advanced } = page
  const formSchema = useMemo(
    () => prune(section(schema, path), schema, { advanced, omit, driver }),
    [schema, path, advanced, omit, driver],
  )
  const form = (
    <SchemaForm
      schema={formSchema}
      value={getIn(page.value, path)}
      onChange={(next) => page.update((c) => setIn(c, path, applyForm(getIn(c, path), next, formSchema)))}
    />
  )
  if (!title) return form
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>{form}</CardContent>
    </Card>
  )
}
