import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const VOICE = ['channels', 'voice']
const DISCORD = ['channels', 'discord']
const SATELLITE = ['channels', 'satellite']
const DEVICES = ['devices']
const ANNOUNCE = ['announce']

export default function ChannelsPage() {
  const s = useSettings([VOICE, DISCORD, SATELLITE, ANNOUNCE])
  if (s.loading) return <PageSkeleton title="Channels" />
  return (
    <SettingsPage title="Channels" page={s.page}>
      <SectionForm page={s.page} path={VOICE} title="Voice" />
      <SectionForm page={s.page} path={DISCORD} title="Discord" />
      {/* devices are set in config.yaml until the Voice PE is proven; docs/voice-pe.md */}
      <SectionForm page={s.page} path={SATELLITE} title="Satellites" omit={DEVICES} />
      <SectionForm page={s.page} path={ANNOUNCE} title="Announcements" />
    </SettingsPage>
  )
}
