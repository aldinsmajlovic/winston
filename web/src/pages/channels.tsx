import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const VOICE = ['channels', 'voice']
const TELEGRAM = ['channels', 'telegram']

export default function ChannelsPage() {
  const s = useSettings([VOICE, TELEGRAM])
  if (s.loading) return <PageSkeleton title="Channels" />
  return (
    <SettingsPage title="Channels" page={s.page}>
      <SectionForm page={s.page} path={VOICE} title="Voice" />
      <SectionForm page={s.page} path={TELEGRAM} title="Telegram" />
    </SettingsPage>
  )
}
