import { $api, type Presets } from '@/api/client'
import { ClonedVoices } from '@/components/cloned-voices'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SlotCard } from '@/components/slot-card'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { useVoicePreviews } from '@/hooks/use-voice-previews'

const TTS = ['tts']
const STT = ['stt']
const VAD = ['vad']
const ENGINE = ['engines', 'tts']
// The Hearing card's engine picker sets the adapter; the field would repeat it.
const PROVIDER = ['provider']

/** How Winston speaks and hears: the speech engine and voice, the speech-to-text adapter, turn taking. */
export default function VoicePage() {
  const s = useSettings([TTS, STT, VAD, ENGINE])
  const presets = $api.useQuery('get', '/api/presets')
  if (s.loading || !presets.data) return <PageSkeleton title="Voice" />
  return <Voice page={s.page} presets={presets.data} />
}

function Voice({ page, presets }: { page: SettingsState; presets: Presets }) {
  const previews = useVoicePreviews(page)
  const engine = page.value.engines?.tts
  return (
    <SettingsPage title="Voice" page={page}>
      <SlotCard page={page} kind="tts" title="Speaking" presets={presets.tts}>
        <SectionForm page={page} path={TTS} adornments={previews} />
      </SlotCard>
      <ClonedVoices current={page.value.tts?.voice} />
      <SlotCard page={page} kind="stt" title="Hearing" presets={presets.stt}>
        <SectionForm page={page} path={STT} omit={PROVIDER} />
      </SlotCard>
      {page.advanced ? <SectionForm page={page} path={VAD} title="Turn taking" /> : null}
      {page.advanced && engine ? <SectionForm page={page} path={ENGINE} title="Speech engine" /> : null}
    </SettingsPage>
  )
}
