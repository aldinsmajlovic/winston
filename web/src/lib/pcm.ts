/** Audio scheduled on one AudioContext: where it ends and the pieces still to play, so a later body
 * joins without a gap and a stop silences all of it. */
export type Timeline = { until: number; sources: Set<AudioBufferSourceNode> }

export const timeline = (): Timeline => ({ until: 0, sources: new Set() })

/** Stop everything scheduled and start the next body from now. */
export function silence(line: Timeline) {
  for (const source of line.sources) source.stop()
  line.sources.clear()
  line.until = 0
}

/** Play a streamed body of 16-bit mono PCM (`/api/speak`) as it arrives, right after what the
 * timeline already holds. `onFirst` gets the AudioContext time the first samples start at. Returns
 * when the body has been read, not when it has finished playing (see `timeline.until`). */
export async function playPcm(response: Response, audio: AudioContext, line: Timeline, onFirst?: (at: number) => void) {
  if (!response.body) return
  const rate = Number(response.headers.get('x-sample-rate')) || 24000
  const reader = response.body.getReader()
  let carry: Uint8Array = new Uint8Array(0) // an odd byte left over from a chunk, half a sample
  let first = true
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
    const at = Math.max(line.until, audio.currentTime + 0.05) // right after the previous piece: no gap
    source.start(at)
    if (first) onFirst?.(at)
    first = false
    line.until = at + buffer.duration
    line.sources.add(source)
    source.onended = () => line.sources.delete(source)
  }
}
