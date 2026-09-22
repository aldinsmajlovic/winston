import type { ReactNode } from 'react'
import { Page } from '@/components/page'
import { AdvancedToggle, SaveControls } from '@/components/save-controls'
import type { SettingsState } from '@/hooks/use-settings'

type Props = { title: string; page: SettingsState; actions?: ReactNode; children: ReactNode }

/** Template: settings page. Basic fields first, Advanced behind a toggle, one Save. */
export function SettingsPage({ title, page, actions, children }: Props) {
  return (
    <Page
      title={title}
      actions={
        <>
          {actions}
          <AdvancedToggle checked={page.advanced} onChange={page.setAdvanced} />
          <SaveControls dirty={page.dirty} saving={page.saving} onSave={page.save} onReset={page.reset} />
        </>
      }
    >
      {children}
    </Page>
  )
}
