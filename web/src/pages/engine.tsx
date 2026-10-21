import { Navigate, useParams } from 'react-router'
import { useStatus } from '@/api/status'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { StatusLine } from '@/components/status-line'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'
import { PartStateLine } from '@/pages/status'

/** Brain, Speaking and Hearing set their engines on their own pages. */
const slotPage = (name: string) => (name === 'llm' ? '/brain' : name === 'tts' || name === 'stt' ? '/voice' : null)

/** A custom engine: its state with Log, Restart and Stop, then how it runs, every field of its driver
 * open: the page is itself its How it runs. */
export default function EnginePage() {
  const { name = '' } = useParams()
  const path = ['engines', name]
  const s = useSettings()
  const status = useStatus()
  const slot = slotPage(name)
  if (slot) return <Navigate to={slot} replace />
  if (s.loading || !status.data) return <PageSkeleton title={name} />
  const engine = s.page.value.engines?.[name]
  if (!engine) return <Navigate to="/status" replace />
  const part = status.data.engines.find((e) => e.name === name)
  return (
    <SettingsPage
      title={name}
      back={{ to: '/status', label: 'Status' }}
      intro={`A server ${status.data.assistant} starts and checks; nothing uses it until a setting points at its address.`}
      page={s.page}
    >
      {part ? <PartStateLine part={part} /> : <StatusLine tone="muted">{engine.enabled === false ? 'Off.' : 'Not saved yet.'}</StatusLine>}
      <SectionForm page={s.page} path={path} title="How it runs" />
    </SettingsPage>
  )
}
