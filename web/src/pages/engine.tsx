import { ArrowLeft } from 'lucide-react'
import { Navigate, useNavigate, useParams } from 'react-router'
import { IconButton } from '@/components/icon-button'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { SettingsPage } from '@/components/templates/settings-page'
import { useSettings } from '@/hooks/use-settings'

export default function EnginePage() {
  const { name = '' } = useParams()
  const navigate = useNavigate()
  const path = ['engines', name]
  const s = useSettings([path])
  if (s.loading) return <PageSkeleton title={name} />
  const engine = s.page.value.engines?.[name]
  if (!engine) return <Navigate to="/engines" replace />
  return (
    <SettingsPage
      title={name}
      page={s.page}
      actions={
        <IconButton label="Engines" size="icon" onClick={() => navigate('/engines')}>
          <ArrowLeft />
        </IconButton>
      }
    >
      <SectionForm page={s.page} path={path} />
    </SettingsPage>
  )
}
