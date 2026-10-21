import { useEffect } from 'react'
import { toast } from 'sonner'
import { errorText, fetchClient } from '@/api/client'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/hooks/use-confirm'
import { useSecretStatus, useSecretsDraft } from '@/hooks/use-secrets'

type Props = {
  id?: string
  /** The extension and its `Account` setting. */
  ext: string
  field: string
  /** The variable that holds the refresh token once connected. */
  name: string
  /** What the account is ("Spotify account"), for the confirmation and toasts. */
  label: string
  disabled?: boolean
}

/** Where the service sends the browser back after its sign-in, which its app must list: winstond on the
 * loopback address, since keys are only set from this computer and services take plain http only there. */
function callback(): string {
  return `http://127.0.0.1:${location.port || (location.protocol === 'https:' ? '443' : '80')}/api/accounts/callback`
}

async function copyCallback() {
  try {
    await navigator.clipboard.writeText(callback())
    toast.success('Address copied')
  } catch {
    toast(`Copy this address: ${callback()}`)
  }
}

/** An account the extension signs in to: Connect opens the service's sign-in in a new tab, which comes
 * back to winstond with the sign-in kept in data/.env; then Connected, with Disconnect. Acts at once,
 * like a key's Remove, never through Save. */
export function AccountField({ id, ext, field, name, label, disabled }: Props) {
  const ask = useConfirm()
  const status = useSecretStatus()
  const draft = useSecretsDraft()
  const connected = status.byName.get(name)?.set === true
  const { refetch } = status

  // Back from the sign-in tab: whether it took.
  useEffect(() => {
    const again = () => void refetch()
    window.addEventListener('focus', again)
    return () => window.removeEventListener('focus', again)
  }, [refetch])

  const connect = async () => {
    const tab = window.open('', '_blank')       // opened on the click itself, so no popup blocker stops it
    const { data, error } = await fetchClient.POST('/api/extensions/{ext_id}/accounts/{field}', {
      params: { path: { ext_id: ext, field } },
    })
    if (error || !data) {
      tab?.close()
      toast.error(errorText(error))
      return
    }
    if (tab) {
      tab.opener = null
      tab.location.href = data.url
    } else {
      location.href = data.url
    }
  }

  const disconnect = async () => {
    const choice = await ask({
      title: `Disconnect the ${label}?`,
      lines: ['Winston stops reading it now, without waiting for Save. The service keeps the app allowed until you remove it there.'],
      choices: [{ label: 'Disconnect', value: 'yes', destructive: true }],
    })
    if (!choice) return
    const why = await draft.remove(name)
    if (why) {
      toast.error(why)
      return
    }
    toast.success(`Disconnected the ${label}`)
  }

  if (status.isLoading) return null
  return connected ? (
    <div className="flex items-center gap-2">
      <span className="text-sm text-success">Connected</span>
      <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={() => void disconnect()}>
        Disconnect
      </Button>
    </div>
  ) : (
    <div className="flex flex-col items-start gap-1.5">
      <Button id={id} type="button" variant="outline" size="sm" disabled={disabled} onClick={() => void connect()}>
        Connect
      </Button>
      <p className="text-sm text-muted-foreground">
        Your app must list {callback()} as a redirect URI.{' '}
        <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => void copyCallback()}>
          Copy it
        </Button>
      </p>
    </div>
  )
}
