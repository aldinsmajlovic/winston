import { useRef, useState } from 'react'
import { Play, ShieldCheck, Square, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { $api, errorText } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { InfoTip } from '@/components/info-tip'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useConfirm } from '@/hooks/use-confirm'

type Props = { current?: string }

const TIP =
  'Say "Winston, clone my friend\'s voice" with your friend there. They agree out loud first, then talk for ' +
  'about fifteen seconds. "Back to your own voice" switches back.'

/** Friends' voices Winston cloned when asked, each with the recording of them agreeing to it. */
export function ClonedVoices({ current }: Props) {
  const clones = $api.useQuery('get', '/api/voices/clones')
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

  const rows = clones.data ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          Cloned voices
          <InfoTip text={TIP} />
        </CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">None yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Recorded</TableHead>
                <TableHead>Length</TableHead>
                <TableHead className="w-0" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.slug}>
                  <TableCell>
                    <span className="flex items-center gap-2 font-medium">
                      {c.name}
                      {current === c.voice ? <Badge variant="secondary">Winston&apos;s voice</Badge> : null}
                    </span>
                  </TableCell>
                  <TableCell>{new Date(c.created * 1000).toLocaleDateString()}</TableCell>
                  <TableCell>{Math.round(c.seconds)} s</TableCell>
                  <TableCell>
                    <span className="flex justify-end">
                      <IconButton label="Play the voice" onClick={() => play(c.slug, 'voice')}>
                        {playing === `${c.slug}/voice` ? <Square /> : <Play />}
                      </IconButton>
                      <IconButton label={`Play the consent: "${c.consent_text}"`} onClick={() => play(c.slug, 'consent')}>
                        {playing === `${c.slug}/consent` ? <Square /> : <ShieldCheck />}
                      </IconButton>
                      <IconButton label="Delete" onClick={() => void del(c.slug, c.name)}>
                        <Trash2 />
                      </IconButton>
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
