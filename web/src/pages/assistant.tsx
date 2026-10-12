import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const ASSISTANT = ['assistant']
const REGION = ['region']
const MEMORY = ['memory']

export default function AssistantPage() {
  const s = useSettings([ASSISTANT, REGION, MEMORY])
  if (s.loading) return <PageSkeleton title="Assistant" />
  return (
    <SettingsPage title="Assistant" page={s.page}>
      <SectionForm page={s.page} path={ASSISTANT} />
      <SectionForm page={s.page} path={REGION} title="Region" />
      <SectionForm page={s.page} path={MEMORY} title="Memory" />
    </SettingsPage>
  )
}
