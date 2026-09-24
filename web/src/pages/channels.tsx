import { $api } from '@/api/client'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { StateIcon } from '@/components/state-icon'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const VOICE = ['channels', 'voice']
const DISCORD = ['channels', 'discord']
const SATELLITE = ['channels', 'satellite']
const DEVICES = ['devices']
const ANNOUNCE = ['announce']

/** A channel's state from /api/status beside its card title, the detail (a missing token) on hover.
 * A channel that is switched off is not in the status, and shows nothing. */
function ChannelState({ name }: { name: string }) {
  const status = $api.useQuery('get', '/api/status')
  const channel = status.data?.engines.find((e) => e.kind === 'channel' && e.name === name)
  return channel ? <StateIcon state={channel.state} detail={channel.detail} /> : null
}

export default function ChannelsPage() {
  const s = useSettings([VOICE, DISCORD, SATELLITE, ANNOUNCE])
  if (s.loading) return <PageSkeleton title="Channels" />
  return (
    <SettingsPage title="Channels" page={s.page}>
      <SectionForm page={s.page} path={VOICE} title="Voice" status={<ChannelState name="voice" />} />
      <SectionForm page={s.page} path={DISCORD} title="Discord" status={<ChannelState name="discord" />} />
      {/* devices are set in config.yaml until the Voice PE is proven; docs/voice-pe.md */}
      <SectionForm page={s.page} path={SATELLITE} title="Satellites" omit={DEVICES} status={<ChannelState name="satellite" />} />
      <SectionForm page={s.page} path={ANNOUNCE} title="Announcements" />
    </SettingsPage>
  )
}
