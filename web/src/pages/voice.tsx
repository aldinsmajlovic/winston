import { $api } from '@/api/client'
import { ClonedVoices } from '@/components/cloned-voices'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SlotCard } from '@/components/slot-card'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const TTS = ['tts']
const STT = ['stt']
const VAD = ['vad']
const ENGINE = ['engines', 'tts']
const PROVIDER = ['provider']
const STREAMING = ['streaming']

/** How Winston speaks and hears: the speech engine and voice, the speech-to-text adapter, turn taking. */
export default function VoicePage() {
  const s = useSettings([TTS, STT, VAD, ENGINE])
  const presets = $api.useQuery('get', '/api/presets')
  if (s.loading || !presets.data) return <PageSkeleton title="Voice" />
  const { page } = s
  const engine = page.value.engines?.tts
  // The streaming settings reach the server through its command line; hide them for servers that ignore them
  const streams = (engine?.cmd ?? []).some((arg) => arg.includes('{tts.streaming.'))
  return (
    <SettingsPage title="Voice" page={page}>
      <SlotCard page={page} kind="tts" title="Speaking" presets={presets.data.tts}>
        <SectionForm page={page} path={TTS} omit={streams ? undefined : STREAMING} />
      </SlotCard>
      <ClonedVoices current={page.value.tts?.voice} />
      <SlotCard page={page} kind="stt" title="Hearing" presets={presets.data.stt}>
        <SectionForm page={page} path={STT} omit={PROVIDER} />
      </SlotCard>
      {page.advanced ? <SectionForm page={page} path={VAD} title="Turn taking" /> : null}
      {page.advanced && engine ? (
        <SectionForm page={page} path={ENGINE} title="Speech engine" driver={engine.driver} />
      ) : null}
    </SettingsPage>
  )
}
