import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const LOGGING = ['logging']
const CONTROL = ['control']

export default function SystemPage() {
  const s = useSettings([LOGGING, CONTROL])
  if (s.loading) return <PageSkeleton title="System" />
  return (
    <SettingsPage title="System" page={s.page}>
      <SectionForm page={s.page} path={LOGGING} title="Logging" />
      <SectionForm page={s.page} path={CONTROL} title="Control" />
    </SettingsPage>
  )
}
