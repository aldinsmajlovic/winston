import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const ASSISTANT = ['assistant']
const REGION = ['region']

export default function AssistantPage() {
  const s = useSettings([ASSISTANT, REGION])
  if (s.loading) return <PageSkeleton title="Assistant" />
  return (
    <SettingsPage title="Assistant" page={s.page}>
      <SectionForm page={s.page} path={ASSISTANT} />
      <SectionForm page={s.page} path={REGION} title="Region" />
    </SettingsPage>
  )
}
