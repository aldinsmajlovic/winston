import { $api } from '@/api/client'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SlotCard } from '@/components/slot-card'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const LLM = ['llm']
const ENGINE = ['engines', 'llm']

/** The brain: what runs it, which model, whether it thinks; sampling and the engine under Advanced. */
export default function BrainPage() {
  const s = useSettings([LLM, ENGINE])
  const presets = $api.useQuery('get', '/api/presets')
  if (s.loading || !presets.data) return <PageSkeleton title="Brain" />
  const { page } = s
  const engine = page.value.engines?.llm
  return (
    <SettingsPage title="Brain" page={page}>
      <SlotCard page={page} kind="llm" title="Model" presets={presets.data.llm}>
        <SectionForm page={page} path={LLM} />
      </SlotCard>
      {page.advanced && engine ? <SectionForm page={page} path={ENGINE} title="Engine" /> : null}
    </SettingsPage>
  )
}
