import { $api, errorText, type Release } from '@/api/client'
import type { useUpdate } from '@/api/update'
import { Fold } from '@/components/fold'
import { StatusLine } from '@/components/status-line'
import { LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useConfirm } from '@/hooks/use-confirm'
import { useRegion } from '@/hooks/use-region'
import { since } from '@/lib/format'


/** One release's changes: its version and date, then each change. */
function Changes({ release }: { release: Release }) {
  const region = useRegion()
  return (
    <div className="flex flex-col gap-1">
      <span className="font-medium">
        {release.version}
        {/^\d{4}-\d\d-\d\d$/.test(release.date) ? <span className="font-normal text-muted-foreground">, {region.date(new Date(`${release.date}T12:00`))}</span> : null}
      </span>
      <ul className="ml-5 list-disc text-sm text-muted-foreground">
        {release.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

/** Status's Updates card: this version and the latest, what is new, Update with its confirm, and how far
 * an update has got. In a git clone it only says so: git updates a clone. */
export function UpdateCard({ update, name, downtime }: { update: ReturnType<typeof useUpdate>; name: string; downtime: string }) {
  const ask = useConfirm()
  const start = $api.useMutation('post', '/api/update', { onSuccess: () => void update.query.refetch() })
  const check = $api.useMutation('post', '/api/update/check', { onSuccess: () => void update.query.refetch() })
  const u = update.query.data
  if (!u) return null
  const newer = !u.clone && !!u.latest && u.latest !== u.version
  const go = async () => {
    const lines = [
      `${name} restarts and cannot hear, answer or speak for ${downtime}.`,
      'Your settings, memory and voices stay as they are.',
      `Version ${u.version} stays on his computer, so you can go back to it.`,
    ]
    if (await ask({ title: `Update to ${u.latest}?`, lines, choices: [{ label: 'Update', value: 'yes' }] })) start.mutate({ body: {} })
  }

  let line
  if (update.waitingFor || u.stage === 'restarting') {
    line = <StatusLine tone="muted">{`Version ${update.waitingFor ?? u.detail} is installed and ${name} is restarting. This page reloads when he is back.`}</StatusLine>
  } else if (u.stage === 'installing') {
    line = <StatusLine tone="muted">{`Updating to version ${u.latest}: ${u.detail.charAt(0).toLowerCase()}${u.detail.slice(1)}.`}</StatusLine>
  } else if (u.stage === 'installed') {
    line = <StatusLine tone="warning">{`Version ${u.detail} is installed. Restart ${name} to run it.`}</StatusLine>
  } else if (start.isError || u.stage === 'error') {
    line = (
      <StatusLine tone="error" action={{ label: 'Try again', onClick: () => void go() }}>
        {start.isError ? `Not updated: ${errorText(start.error)}` : `Not updated: ${u.detail}`}
      </StatusLine>
    )
  } else if (u.clone) {
    line = (
      <StatusLine tone="muted">
        {`Version ${u.version}, from a copy of the source code, which you update with git.`}
        {u.latest ? ` The latest release is ${u.latest}.` : ''}
      </StatusLine>
    )
  } else if (newer) {
    line = (
      <StatusLine tone="muted" action={{ label: 'Update', onClick: () => void go() }}>
        {`Version ${u.latest} is out. This is ${u.version}.`}
      </StatusLine>
    )
  } else if (u.latest) {
    line = <StatusLine tone="ok">{`Version ${u.version}, the latest.`}</StatusLine>
  } else {
    line = <StatusLine tone="muted">{`Version ${u.version}.`}</StatusLine>
  }

  return (
    <Card id="updates">
      <CardHeader>
        <CardTitle>Updates</CardTitle>
        <CardDescription>
          {name} checks for a new release every few hours{u.checked ? `, last ${since(u.checked)} ago` : ''}.
        </CardDescription>
        {u.stage === 'installing' || u.stage === 'restarting' || update.waitingFor ? null : (
          <CardAction>
            <Button variant="outline" size="sm" disabled={check.isPending} onClick={() => check.mutate({})}>
              {check.isPending ? <LoaderCircle data-icon="inline-start" className="animate-spin" /> : null}
              Check now
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {line}
        {u.error && !u.stage ? <StatusLine tone="muted">{u.error}</StatusLine> : null}
        {newer && !u.stage && u.notes.length ? (
          <div className="flex flex-col gap-3">
            <span className="text-sm font-medium">What is new</span>
            {u.notes.map((r) => (
              <Changes key={r.version} release={r} />
            ))}
          </div>
        ) : null}
        {!newer && u.current?.items.length ? (
          <Fold title={`What is new in ${u.current.version}`}>
            <Changes release={u.current} />
          </Fold>
        ) : null}
      </CardContent>
    </Card>
  )
}
