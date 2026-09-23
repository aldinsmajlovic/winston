import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const VOICE = ['channels', 'voice']
const DISCORD = ['channels', 'discord']
const ANNOUNCE = ['announce']

export default function ChannelsPage() {
  const s = useSettings([VOICE, DISCORD, ANNOUNCE])
  if (s.loading) return <PageSkeleton title="Channels" />
  return (
    <SettingsPage title="Channels" page={s.page}>
      <SectionForm page={s.page} path={VOICE} title="Voice" />
      <SectionForm page={s.page} path={DISCORD} title="Discord" />
      <SectionForm page={s.page} path={ANNOUNCE} title="Announcements" />
    </SettingsPage>
  )
}
