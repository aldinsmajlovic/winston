import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const MEMORY = ['memory']
const LOGGING = ['logging']
const CONTROL = ['control']

export default function SystemPage() {
  const s = useSettings([MEMORY, LOGGING, CONTROL])
  if (s.loading) return <PageSkeleton title="System" />
  return (
    <SettingsPage title="System" page={s.page}>
      <SectionForm page={s.page} path={MEMORY} title="Memory" />
      <SectionForm page={s.page} path={LOGGING} title="Logging" />
      <SectionForm page={s.page} path={CONTROL} title="Control" />
    </SettingsPage>
  )
}
