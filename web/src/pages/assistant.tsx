import { Link } from 'react-router'
import { $api, errorText } from '@/api/client'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useSettings } from '@/hooks/use-settings'

const ASSISTANT = ['assistant']
const REGION = ['region']
const MEMORY = ['memory']
const TURNS = ['logging', 'turns']

/** A row in a section's form for what is edited on its own page: what it holds now, and a link there. */
function LinkRow({ label, text, to, link }: { label: string; text: string; to: string; link: string }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm leading-none font-medium">{label}</span>
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{text}</span>
        <Button variant="outline" size="sm" asChild>
          <Link to={to}>{link}</Link>
        </Button>
      </div>
    </div>
  )
}

/** Who he is (name, language, the start of his personality), the Region he speaks for, and what he
 * keeps about you (memory and recordings), with links to the pages that edit what is not config. */
export default function AssistantPage() {
  const s = useSettings()
  const persona = $api.useQuery('get', '/api/persona')
  const memory = $api.useQuery('get', '/api/memory')
  if (s.loading) return <PageSkeleton title="Assistant" />
  const rule = persona.data ? (persona.data.behaviour[0] ?? 'No rules yet.') : ''
  // Expired facts are kept but no longer in his mind, so they do not count.
  const facts = memory.data?.topics.reduce((n, t) => n + t.facts.filter((f) => !f.expired).length, 0)
  const noted = facts === undefined ? '' : `${facts} ${facts === 1 ? 'fact' : 'facts'}`
  return (
    <SettingsPage
      title="Assistant"
      intro="His name, manner and language, and what he keeps about you on this computer."
      page={s.page}
    >
      <Card>
        <CardHeader>
          <CardTitle>Who he is</CardTitle>
        </CardHeader>
        <CardContent>
          <SectionForm page={s.page} path={ASSISTANT}>
            <LinkRow
              label="Personality"
              text={persona.isError ? errorText(persona.error) : rule}
              to="/assistant/personality"
              link="Edit personality"
            />
          </SectionForm>
        </CardContent>
      </Card>
      <SectionForm page={s.page} path={REGION} title="Region" />
      <SectionForm page={s.page} path={MEMORY} title="What he remembers">
        <LinkRow label="Memory" text={memory.isError ? errorText(memory.error) : noted} to="/memory" link="Open memory" />
      </SectionForm>
      <SectionForm page={s.page} path={TURNS} title="Recordings" />
    </SettingsPage>
  )
}
