import { useRef, useState } from 'react'
import { CircleAlert, CircleCheck, CircleHelp, Play, Square, type LucideIcon } from 'lucide-react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { $api, errorText } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { RowMenu } from '@/components/row-menu'
import { StatusTip } from '@/components/status-tip'
import { DataTable, type Column } from '@/components/templates/list-page'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useConfirm } from '@/hooks/use-confirm'
import { takePlayback } from '@/hooks/use-preview'
import { useRegion } from '@/hooks/use-region'

type Props = { current?: string; onUse: (voice: string) => void }
type Clone = NonNullable<ReturnType<typeof useClones>['data']>[number]

/** A word with its icon; the details open from the icon. */
function Match({ icon, className, word, lines }: { icon: LucideIcon; className: string; word: string; lines: string[] }) {
  return (
    <span className="flex items-center gap-1.5">
      <StatusTip icon={icon} className={className} label={word} lines={lines} />
      {word}
    </span>
  )
}

/** Whether the voice giving permission was the voice recorded: the score, when, and what hearing made of
 * the words open from its icon. */
function VoiceMatch({ clone: c }: { clone: Clone }) {
  const region = useRegion()
  const heard = `Heard: "${c.consent_text}"`
  if (c.consent_score == null) {
    return (
      <Match
        icon={CircleHelp}
        className="text-warning"
        word="Not checked"
        lines={['Recorded before the same-voice check, or while its model was not available', heard]}
      />
    )
  }
  const needed = c.same_voice_at ?? 0.55
  const at = c.consent_checked_at
  const lines = [
    `Score ${c.consent_score.toFixed(2)}, ${needed.toFixed(2)} needed`,
    ...(at ? [`Checked ${region.date(at)}, ${region.time(at)}`] : []),
    heard,
  ]
  return c.consent_score >= needed ? (
    <Match icon={CircleCheck} className="text-success" word="Checked" lines={lines} />
  ) : (
    <Match icon={CircleAlert} className="text-destructive" word="Below the threshold" lines={lines} />
  )
}

const useClones = () => $api.useQuery('get', '/api/voices/clones')

/** Your voices: the ones he cloned when asked, each with the recording of the speaker agreeing to it.
 * Play one, use it, or from its menu play the permission or delete it. */
export function ClonedVoices({ current, onUse }: Props) {
  const name = $api.useQuery('get', '/api/status').data?.assistant || 'him'
  const region = useRegion()
  const clones = useClones()
  const remove = $api.useMutation('delete', '/api/voices/clones/{slug}')
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
      lines: ['The voice clip and the recording of their permission both go.'],
      choices: [{ label: 'Delete', value: 'yes', destructive: true }],
    })
    if (!choice) return
    stop()
    remove.mutate({ params: { path: { slug } } }, { onSuccess: () => void clones.refetch() })
  }

  const columns: Column<Clone>[] = [
    { key: 'name', title: 'Name', render: (c) => <span className="font-medium">{c.name}</span> },
    { key: 'created', title: 'Recorded', render: (c) => region.date(c.created) },
    { key: 'seconds', title: 'Length', render: (c) => `${Math.round(c.seconds)} s` },
    { key: 'match', title: 'Voice match', render: (c) => <VoiceMatch clone={c} /> },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your voices</CardTitle>
        <CardDescription>Voices made from a short recording, with the speaker's spoken permission.</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          rows={clones.data ?? []}
          rowKey={(c) => c.slug}
          empty={
            <>
              No voices of your own yet. On the{' '}
              <Link to="/" className="underline underline-offset-4 hover:text-foreground">
                Talk page
              </Link>{' '}
              in this browser, tell {name}: clone my voice. The person agrees out loud first, then talks for about 15
              seconds.
            </>
          }
          actions={(c) => (
            <span className="inline-flex items-center gap-2">
              <IconButton label={`Play ${c.name}'s voice`} onClick={() => play(c.slug, 'voice')}>
                {playing === `${c.slug}/voice` ? <Square /> : <Play />}
              </IconButton>
              {current === c.voice ? (
                <span className="text-sm text-muted-foreground">In use</span>
              ) : (
                <Button type="button" variant="outline" size="sm" onClick={() => onUse(c.voice)}>
                  Use
                </Button>
              )}
              <RowMenu
                name={c.name}
                items={[
                  {
                    label: playing === `${c.slug}/consent` ? 'Stop their permission' : 'Play their permission',
                    onSelect: () => play(c.slug, 'consent'),
                  },
                  { label: 'Delete', destructive: true, onSelect: () => void del(c.slug, c.name) },
                ]}
              />
            </span>
          )}
        />
      </CardContent>
    </Card>
  )
}
