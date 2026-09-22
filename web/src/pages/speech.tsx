import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

const STT = ['stt']
const TTS = ['tts']
const VAD = ['vad']

export default function SpeechPage() {
  const s = useSettings([STT, TTS, VAD])
  if (s.loading) return <PageSkeleton title="Speech" />
  return (
    <SettingsPage title="Speech" page={s.page}>
      <SectionForm page={s.page} path={STT} title="Speech to text" />
      <SectionForm page={s.page} path={TTS} title="Text to speech" />
      <SectionForm page={s.page} path={VAD} title="Voice activity" />
    </SettingsPage>
  )
}
