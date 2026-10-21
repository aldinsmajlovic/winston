import type { ComponentProps } from 'react'
import { Page } from '@/components/page'
import { SaveBar, SaveControls } from '@/components/save-controls'
import type { SettingsState } from '@/hooks/use-settings'

type Props = Omit<ComponentProps<typeof Page>, 'footer'> & { page: SettingsState }

/** Template: settings page. Its commands, then one Save (along the bottom on a phone while there is
 * something to save). */
export function SettingsPage({ page, actions, ...props }: Props) {
  return (
    <Page
      {...props}
      actions={
        <>
          {actions}
          <SaveControls page={page} />
        </>
      }
      footer={page.dirty && <SaveBar page={page} />}
    />
  )
}
