import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { $api, errorText } from '@/api/client'
import { StatusLine } from '@/components/status-line'
import { refreshHomeAssistant, type HAExposure } from '@/hooks/use-home-assistant'

/** The value once it has stopped changing for a moment, so typing an address asks once, not per key. */
function useSettled(value: string, ms = 600): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

/** Whether Home Assistant answers at an address with a token, both unsaved: the setup card's checks and
 * the Connection card's while its address or token is being changed. Without a pasted token the saved
 * one is tried. */
export function HACheck({ url, token }: { url?: string | null; token?: string }) {
  const at = useSettled((url ?? '').trim())
  const pasted = useSettled(token ?? '')
  const check = $api.useQuery(
    'post',
    '/api/providers/home_assistant/check',
    { body: { url: at, token: pasted || null } },
    { enabled: at !== '', retry: false },
  )
  if (at === '') return null
  if (!check.data) {
    return <StatusLine tone="muted">{check.error ? errorText(check.error) : 'Checking'}</StatusLine>
  }
  const { ok, detail = '', version } = check.data
  if (ok) {
    return (
      <div className="flex flex-col gap-2">
        <StatusLine tone="ok">Reachable: Home Assistant {version}</StatusLine>
        <StatusLine tone="ok">Token accepted</StatusLine>
      </div>
    )
  }
  // The token is read before anything is asked, and refused only by a Home Assistant that answered.
  if (detail.endsWith('is not set')) return <StatusLine tone="muted">Paste the access token to check the connection.</StatusLine>
  if (detail.startsWith('Token')) {
    return (
      <div className="flex flex-col gap-2">
        <StatusLine tone="ok">Reachable</StatusLine>
        <StatusLine tone="error">Token refused. Make a new one in Home Assistant and paste it.</StatusLine>
      </div>
    )
  }
  return <StatusLine tone="error">Can't reach Home Assistant at this address.</StatusLine>
}

type Props = { exposure: { data?: HAExposure; error: unknown; isLoading: boolean } }

/** The saved connection in words: which Home Assistant answered, and a warning when his actions cannot
 * come from it or the token may not read rooms and names; Check again reads it all again. */
export function HAConnectionStatus({ exposure }: Props) {
  const client = useQueryClient()
  const again = { label: 'Check again', onClick: () => refreshHomeAssistant(client) }
  const data = exposure.data
  if (exposure.isLoading) return <StatusLine tone="muted">Checking</StatusLine>
  if (!data || !data.ok) {
    return (
      <StatusLine tone="error" action={again}>
        {data ? data.detail : errorText(exposure.error)}
      </StatusLine>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <StatusLine tone="ok" action={again}>
        Connected to Home Assistant {data.version}
      </StatusLine>
      {data.mcp_server === false ? (
        <StatusLine tone="warning">Add the Model Context Protocol Server integration in Home Assistant, or he has no actions there.</StatusLine>
      ) : null}
      {!data.registry ? <StatusLine tone="warning">Needs an administrator's token to read rooms and names.</StatusLine> : null}
    </div>
  )
}
