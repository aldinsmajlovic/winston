import { $api, type Presets } from '@/api/client'
import { ClonedVoices } from '@/components/cloned-voices'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { runsHere, SlotCard } from '@/components/slot-card'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { useVoicePreviews } from '@/hooks/use-voice-previews'
import { setIn } from '@/lib/schema'

const TTS = ['tts']
const STT = ['stt']

/** How he sounds and hears: what runs speaking and its voice, the voices cloned by asking, what runs
 * hearing and the names it should get right; how each runs folded at the bottom of its card. */
export default function VoicePage() {
  const s = useSettings()
  const presets = $api.useQuery('get', '/api/presets')
  if (s.loading || !presets.data) return <PageSkeleton title="Voice" />
  return <Voice page={s.page} presets={presets.data} />
}

function Voice({ page, presets }: { page: SettingsState; presets: Presets }) {
  const previews = useVoicePreviews(page)
  return (
    <SettingsPage title="Voice" intro="How he sounds and how he hears you." page={page}>
      <SlotCard page={page} kind="tts" title="Speaking" presets={presets.tts}>
        <SectionForm page={page} path={TTS} omit={runsHere('tts', page.value)} adornments={previews} />
      </SlotCard>
      <ClonedVoices current={page.value.tts?.voice} onUse={(voice) => page.update((c) => setIn(c, ['tts', 'voice'], voice))} />
      <SlotCard page={page} kind="stt" title="Hearing" presets={presets.stt}>
        <SectionForm page={page} path={STT} omit={runsHere('stt', page.value)} />
      </SlotCard>
    </SettingsPage>
  )
}
