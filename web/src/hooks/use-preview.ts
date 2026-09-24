import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { errorText } from '@/api/client'
import type { components } from '@/api/schema'
import { playPcm, silence, timeline } from '@/lib/pcm'

export type SpeakBody = components['schemas']['SpeakRequest']

/** What one preview measured: time from the click to the first sound, what streaming the server
 * says it used (X-Streaming), and the tag the page played it under. */
export type Heard = { ms: number; streaming: 'on' | 'off' | 'ignored' | null; tag?: string }

let owner: (() => void) | null = null

/** One preview plays at a time: taking playback stops whatever had it (another play button, a
 * cloned voice's recording). */
export function takePlayback(stop: () => void) {
  const was = owner
  owner = stop
  if (was && was !== stop) was()
}

let shared: AudioContext | null = null

/** Created and resumed from the click that plays, since browsers only start audio from one. */
function audioContext(): AudioContext {
  shared ??= new AudioContext()
  void shared.resume()
  return shared
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** One text spoken once through `/api/speak` with unsaved settings (voice, streaming), played as it
 * streams in, the same way Talk reads replies aloud. */
export function usePreview() {
  const [playing, setPlaying] = useState(false)
  const [heard, setHeard] = useState<Heard | null>(null)
  const abort = useRef<AbortController | null>(null)
  const line = useRef(timeline())

  const stop = useCallback(() => {
    abort.current?.abort()
    abort.current = null
    silence(line.current)
    setPlaying(false)
  }, [])

  // Leaving the page ends its preview rather than letting it play on elsewhere.
  useEffect(() => stop, [stop])

  const play = useCallback(
    async (body: SpeakBody, tag?: string) => {
      stop()
      takePlayback(stop)
      const audio = audioContext()
      const controller = new AbortController()
      abort.current = controller
      setPlaying(true)
      setHeard(null)
      const started = performance.now()
      try {
        const response = await fetch('/api/speak', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })
        if (!response.ok) {
          const problem: unknown = await response.json().catch(() => null)
          throw new Error(problem ? errorText(problem) : `Could not play the sample (${response.status})`)
        }
        const streaming = response.headers.get('x-streaming') as Heard['streaming']
        await playPcm(response, audio, line.current, (at) =>
          setHeard({ ms: performance.now() - started + (at - audio.currentTime) * 1000, streaming, tag }),
        )
        while (!controller.signal.aborted && audio.state === 'running' && audio.currentTime < line.current.until) await wait(100)
      } catch (error) {
        if (!controller.signal.aborted) toast.error(error instanceof Error ? error.message : 'Could not play the sample')
      } finally {
        if (abort.current === controller) {
          abort.current = null
          setPlaying(false)
        }
      }
    },
    [stop],
  )

  return { play, stop, playing, heard }
}
