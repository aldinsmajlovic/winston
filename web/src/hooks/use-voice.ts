import { useCallback, useEffect, useRef, useState } from 'react'
import { PipecatClient } from '@pipecat-ai/client-js'
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport'

export type VoiceStatus = 'off' | 'connecting' | 'on'

/** The browser's mic and speaker, connected to the voice channel over WebRTC through winstond. In a
 * room, the session shares the room's conversation and pauses while the room's box has the mic. */
export function useVoice(onError: (message: string) => void, room = '') {
  const client = useRef<PipecatClient | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)   // Winston's voice; not in the DOM, it only plays
  const [status, setStatus] = useState<VoiceStatus>('off')

  const stop = useCallback(async () => {
    const c = client.current
    client.current = null
    setStatus('off')
    await c?.disconnect()
  }, [])

  const start = useCallback(async () => {
    setStatus('connecting')
    const c = new PipecatClient({
      transport: new SmallWebRTCTransport({
        webrtcRequestParams: { endpoint: '/api/voice/offer', requestData: room ? { room } : undefined },
      }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onTrackStarted: (track, participant) => {
          if (participant?.local || track.kind !== 'audio') return
          audio.current ??= new Audio()
          audio.current.srcObject = new MediaStream([track])
          void audio.current.play()
        },
        onDisconnected: () => {
          if (client.current === c) void stop()
        },
      },
    })
    client.current = c
    try {
      await c.connect()
      if (client.current === c) setStatus('on')
    } catch (error) {
      await stop()
      onError(error instanceof Error ? error.message : 'Could not connect to the voice channel')
    }
  }, [onError, room, stop])

  useEffect(() => () => void client.current?.disconnect(), [])

  return { status, start, stop }
}
