import { useCallback, useEffect, useRef, useState } from 'react'

const KEY = 'winston.readAloud'

function stored(): boolean {
  try {
    return localStorage.getItem(KEY) === 'on'
  } catch {
    return false
  }
}

/** Typed replies read aloud in the browser: `/api/speak` streams PCM from the speech server, played
 * through Web Audio as it arrives. Texts queue and play back to back, the next fetched while the one
 * before still plays. The choice is remembered per browser. */
export function useSpeech(onError: (message: string) => void) {
  const [on, setOn] = useState(stored)
  const [playing, setPlaying] = useState(false)
  const ctx = useRef<AudioContext | null>(null)
  const queue = useRef<string[]>([])
  const busy = useRef(false)
  const abort = useRef<AbortController | null>(null)
  const sources = useRef(new Set<AudioBufferSourceNode>())
  const until = useRef(0) // when the audio scheduled so far ends, in AudioContext time

  /** Browsers only start audio from a click or a key, so the page calls this from one. */
  const prime = useCallback(() => {
    ctx.current ??= new AudioContext()
    void ctx.current.resume()
  }, [])

  const stop = useCallback(() => {
    queue.current = []
    abort.current?.abort()
    for (const source of sources.current) source.stop()
    sources.current.clear()
    until.current = 0
  }, [])

  const play = useCallback(async (text: string, signal: AbortSignal) => {
    const audio = ctx.current
    if (audio?.state !== 'running') return // never unlocked by a click: nothing would play
    const response = await fetch('/api/speak', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text }),
      signal,
    })
    if (!response.ok || !response.body) throw new Error(`Could not read the reply aloud (${response.status})`)
    const rate = Number(response.headers.get('x-sample-rate')) || 24000
    const reader = response.body.getReader()
    let carry: Uint8Array = new Uint8Array(0) // an odd byte left over from a chunk, half a sample
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      const bytes = new Uint8Array(carry.length + value.length)
      bytes.set(carry)
      bytes.set(value, carry.length)
      const whole = bytes.length - (bytes.length % 2)
      carry = bytes.slice(whole)
      if (!whole) continue
      const pcm = new Int16Array(bytes.buffer, 0, whole / 2)
      const buffer = audio.createBuffer(1, pcm.length, rate)
      const samples = buffer.getChannelData(0)
      for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768
      const source = audio.createBufferSource()
      source.buffer = buffer
      source.connect(audio.destination)
      const at = Math.max(until.current, audio.currentTime + 0.05) // right after the previous text: no gap
      source.start(at)
      until.current = at + buffer.duration
      sources.current.add(source)
      source.onended = () => sources.current.delete(source)
    }
  }, [])

  const drain = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    setPlaying(true)
    try {
      let text: string | undefined
      while ((text = queue.current.shift()) !== undefined) {
        const controller = new AbortController()
        abort.current = controller
        try {
          await play(text, controller.signal)
        } catch (error) {
          if (!controller.signal.aborted) onError(error instanceof Error ? error.message : 'Could not read the reply aloud')
        }
        // the next text is fetched while this one still plays; when none is queued, wait out the audio
        while (!queue.current.length && ctx.current && ctx.current.currentTime < until.current) {
          await new Promise((resolve) => setTimeout(resolve, 100))
        }
      }
    } finally {
      busy.current = false
      setPlaying(false)
    }
  }, [onError, play])

  const speak = useCallback(
    (text: string) => {
      if (!text.trim()) return
      queue.current.push(text)
      void drain()
    },
    [drain],
  )

  const toggle = useCallback(() => {
    const next = !on
    setOn(next)
    try {
      localStorage.setItem(KEY, next ? 'on' : 'off')
    } catch {
      // private window or blocked storage: the choice lasts this visit only
    }
    if (next) prime()
    else stop()
  }, [on, prime, stop])

  useEffect(
    () => () => {
      void ctx.current?.close()
      ctx.current = null
    },
    [],
  )

  return { on, playing, toggle, prime, speak, stop }
}
