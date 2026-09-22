import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const LLM = ['llm']

export default function BrainPage() {
  const s = useSettings([LLM])
  if (s.loading) return <PageSkeleton title="Brain" />
  return (
    <SettingsPage title="Brain" page={s.page}>
      <SectionForm page={s.page} path={LLM} />
    </SettingsPage>
  )
}
