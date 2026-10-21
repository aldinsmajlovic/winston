import { $api } from '@/api/client'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { runsHere, SlotCard } from '@/components/slot-card'
import { SettingsPage } from '@/components/templates/settings-page'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useSettings } from '@/hooks/use-settings'

const LLM = ['llm']
const THINKING = ['llm', 'thinking']
const SAMPLING = ['llm', 'sampling']
// Each has its own card below.
const OWN_CARDS = ['thinking', 'sampling']
// How thinking is switched is a fact about the server: it sits in How it runs.
const SWITCH = ['control']

/** The brain: what runs it and which model with how it runs folded at the bottom, whether he thinks
 * before answering, and how he picks his words. */
export default function BrainPage() {
  const s = useSettings()
  const presets = $api.useQuery('get', '/api/presets')
  if (s.loading || !presets.data) return <PageSkeleton title="Brain" />
  const { page } = s
  return (
    <SettingsPage
      title="Brain"
      intro="How he thinks: which model, and whether he thinks before answering."
      page={page}
    >
      <SlotCard page={page} kind="llm" title="What runs his brain" presets={presets.data.llm}>
        <SectionForm page={page} path={LLM} omit={[...OWN_CARDS, ...runsHere('llm', page.value)]} />
      </SlotCard>
      {page.value.llm.thinking?.control === 'none' ? (
        // Nothing to choose: the server thinks or not by itself.
        <Card>
          <CardHeader>
            <CardTitle>Think before answering</CardTitle>
            <CardDescription>
              This server has no thinking switch, so it decides by itself. If yours has one, set How thinking is switched under How it runs, above.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <SectionForm page={page} path={THINKING} title="Think before answering" omit={SWITCH} />
      )}
      <SectionForm page={page} path={SAMPLING} title="How he picks his words" />
    </SettingsPage>
  )
}
