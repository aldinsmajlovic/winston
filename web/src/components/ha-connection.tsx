import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { useSearchParams } from 'react-router'
import { $api, errorText } from '@/api/client'
import { StatusLine } from '@/components/status-line'
import { Button } from '@/components/ui/button'
import { refreshHomeAssistant, type HAExposure } from '@/hooks/use-home-assistant'

/** The page signing in comes back to: the Home Assistant page or the wizard's step. */
export type SignInBack = '/home-assistant' | '/setup/home_assistant'

/** Starts signing in: winstond keeps the sign-in's state and gives Home Assistant's login, which the browser
 * opens; Home Assistant sends it back to `back` once Winston has his own token there. */
function useSignIn(back: SignInBack) {
  const start = $api.useMutation('post', '/api/providers/home_assistant/signin', {
    onSuccess: (data) => window.location.assign(data.authorize),
  })
  return { go: (url: string) => start.mutate({ body: { url: url.trim(), back } }), busy: start.isPending || start.isSuccess }
}

/** Sign in, beside the address: Home Assistant's own login, after which he has his own token there. */
export function HASignInButton({ url, back }: { url?: string | null; back: SignInBack }) {
  const signIn = useSignIn(back)
  const at = (url ?? '').trim()
  return (
    <div className="flex flex-col items-start gap-2">
      <Button type="button" size="sm" disabled={!/^https?:\/\/./.test(at) || signIn.busy} onClick={() => signIn.go(at)}>
        {signIn.busy ? <LoaderCircle className="animate-spin" /> : null}
        Sign in to Home Assistant
      </Button>
      <p className="text-sm text-muted-foreground">Opens its login. Winston then makes his own token there, named Winston, so there is nothing to paste.</p>
    </div>
  )
}

/** Home Assistants that announce themselves on this network, each with Sign in. mDNS does not cross from
 * one network to another, so none found says to type the address. */
export function HADiscovered({ back }: { back: SignInBack }) {
  const found = $api.useQuery('get', '/api/providers/home_assistant/discover', undefined, { retry: false, staleTime: 60_000 })
  const signIn = useSignIn(back)
  if (found.isLoading) return <StatusLine tone="muted">Looking for Home Assistant on your network</StatusLine>
  if (!found.data?.length) {
    return <StatusLine tone="muted">None found on your network. A Home Assistant on another network is not announced here: type its address.</StatusLine>
  }
  return (
    <div className="flex flex-col gap-2">
      {found.data.map((ha) => (
        <StatusLine key={ha.url} tone="ok" action={{ label: 'Sign in', onClick: () => signIn.go(ha.url) }}>
          {`Found ${ha.name}${ha.version ? `, Home Assistant ${ha.version}` : ''}, at ${ha.url}`}
        </StatusLine>
      ))}
    </div>
  )
}

/** How the sign-in went, once, from the address Home Assistant sent the browser back to: whom it signed
 * in as, what that account cannot do, and whether its MCP server was switched on; or why it did not finish. */
export function HASignInResult() {
  const [params, setParams] = useSearchParams()
  const [result] = useState(() => ({
    done: params.get('signin') === 'done',
    name: params.get('name') || 'Its user',
    admin: params.get('admin') !== '0',
    mcp: params.get('mcp') ?? '',
    error: params.get('signin_error'),
  }))
  useEffect(() => {
    if (params.has('signin') || params.has('signin_error')) setParams({}, { replace: true })
  }, [params, setParams])
  if (result.error) return <StatusLine tone="error">{result.error}</StatusLine>
  if (!result.done) return null
  return (
    <div className="flex flex-col gap-2">
      <StatusLine tone="ok">{`Signed in as ${result.name}. He has his own token there now, named Winston.`}</StatusLine>
      {result.mcp === 'switched on' ? <StatusLine tone="ok">Switched on its Model Context Protocol Server, for his actions there.</StatusLine> : null}
      {!result.admin ? (
        <StatusLine tone="warning">
          {`${result.name} is not an administrator in Home Assistant, so he cannot read rooms and names or list its devices${result.mcp === 'off' ? ', and its Model Context Protocol Server needs an administrator to switch on' : ''}.`}
        </StatusLine>
      ) : null}
    </div>
  )
}

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
