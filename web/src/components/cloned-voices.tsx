import { useRef, useState } from 'react'
import { CircleAlert, CircleCheck, CircleHelp, Play, ShieldCheck, Square, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { $api, errorText } from '@/api/client'
import { StatusTip } from '@/components/ha-connection'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { DataTable, type Column } from '@/components/templates/list-page'
import { useConfirm } from '@/hooks/use-confirm'
import { takePlayback } from '@/hooks/use-preview'

type Props = { current?: string }
type Clone = NonNullable<ReturnType<typeof useClones>['data']>[number]

const tip = (name: string) =>
  `Say "${name}, clone my friend's voice" with your friend there. They agree out loud first, then talk for ` +
  'about fifteen seconds. "Back to your own voice" switches back. The consent is a record of who agreed, when, ' +
  'and in which voice. It stops keeping a voice when the one agreeing is not the one recorded; it cannot stop a ' +
  'clip added to data/voices by hand.'

/** The same-voice check as an icon: the words, the score, when and what speech to text heard in its tooltip. */
function Consent({ clone: c }: { clone: Clone }) {
  const heard = `Heard: "${c.consent_text}"`
  if (c.consent_score == null) {
    return (
      <StatusTip
        icon={CircleHelp}
        className="text-warning"
        label="Not checked"
        lines={['Recorded before the same-voice check, or while its model was not available', heard]}
      />
    )
  }
  const lines = [
    `Score ${c.consent_score.toFixed(2)}, ${(c.same_voice_at ?? 0.55).toFixed(2)} needed`,
    c.consent_checked_at ? `Checked ${new Date(c.consent_checked_at * 1000).toLocaleString()}` : null,
    heard,
  ].filter((l): l is string => l !== null)
  return c.consent_score >= (c.same_voice_at ?? 0.55) ? (
    <StatusTip icon={CircleCheck} className="text-success" label="Same voice" lines={lines} />
  ) : (
    <StatusTip icon={CircleAlert} className="text-destructive" label="Below the score a new clone needs now" lines={lines} />
  )
}

const useClones = () => $api.useQuery('get', '/api/voices/clones')

/** Friends' voices the assistant cloned when asked, each with the recording of them agreeing to it. */
export function ClonedVoices({ current }: Props) {
  const name = $api.useQuery('get', '/api/status').data?.assistant ?? 'Winston'
  const clones = useClones()
  const remove = $api.useMutation('delete', '/api/voices/clones/{slug}', { meta: { showsErrors: true } })
  const ask = useConfirm()
  const audio = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)

  const stop = () => {
    audio.current?.pause()
    audio.current = null
    setPlaying(null)
  }
  const play = (slug: string, kind: 'voice' | 'consent') => {
    const key = `${slug}/${kind}`
    const was = playing
    stop()
    if (was === key) return
    takePlayback(stop)
    const a = new Audio(`/api/voices/clones/${encodeURIComponent(slug)}/audio?kind=${kind}`)
    a.onended = stop
    audio.current = a
    setPlaying(key)
    a.play().catch((error: unknown) => {
      stop()
      toast.error(errorText(error))
    })
  }
  const del = async (slug: string, name: string) => {
    const choice = await ask({
      title: `Delete ${name}'s voice?`,
      lines: ['The voice clip and the consent recording both go.'],
      choices: [{ label: 'Delete', value: 'yes', destructive: true }],
    })
    if (!choice) return
    stop()
    try {
      await remove.mutateAsync({ params: { path: { slug } } })
    } catch (error) {
      toast.error(errorText(error))
      return
    }
    void clones.refetch()
  }

  const columns: Column<Clone>[] = [
    {
      key: 'name',
      title: 'Name',
      render: (c) => (
        <span className="flex items-center gap-2 font-medium">
          {c.name}
          {current === c.voice ? <Badge variant="secondary">{`${name}'s voice`}</Badge> : null}
        </span>
      ),
    },
    { key: 'created', title: 'Recorded', render: (c) => new Date(c.created * 1000).toLocaleDateString() },
    { key: 'seconds', title: 'Length', render: (c) => `${Math.round(c.seconds)} s` },
    {
      key: 'consent',
      title: 'Consent',
      // a block, so the icon centres in the row instead of sitting on the text's baseline
      render: (c) => (
        <div className="flex">
          <Consent clone={c} />
        </div>
      ),
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          Cloned voices
          <InfoTip text={tip(name)} />
        </CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          rows={clones.data ?? []}
          rowKey={(c) => c.slug}
          actions={(c) => (
            <>
              <IconButton label="Play the voice" onClick={() => play(c.slug, 'voice')}>
                {playing === `${c.slug}/voice` ? <Square /> : <Play />}
              </IconButton>
              <IconButton label={`Play the consent: "${c.consent_text}"`} onClick={() => play(c.slug, 'consent')}>
                {playing === `${c.slug}/consent` ? <Square /> : <ShieldCheck />}
              </IconButton>
              <IconButton label="Delete" onClick={() => void del(c.slug, c.name)}>
                <Trash2 />
              </IconButton>
            </>
          )}
        />
      </CardContent>
    </Card>
  )
}
