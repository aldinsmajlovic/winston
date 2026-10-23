import type { ReactNode } from 'react'
import { CircleAlert } from 'lucide-react'
import { $api, type Config } from '@/api/client'
import { useConfig } from '@/api/config'
import { PreviewButton } from '@/components/preview-button'
import { StatusTip } from '@/components/status-tip'
import { usePreview } from '@/hooks/use-preview'
import type { SettingsState } from '@/hooks/use-settings'
import { deepEqual, section } from '@/lib/schema'

// Long enough to pass the first chunk and the doubling after it, so the joins can be heard.
const SAMPLE =
  'The main room lights are on at half brightness, the front door is locked, and tomorrow looks dry with a ' +
  "light wind from the west, so it's a good day to open the windows for an hour in the morning."

// The running server took the sample without the streaming settings (it predates them).
const IGNORED = 'The running speech server did not use these streaming settings; restart speaking to hear them'

const NOT_RUNNING = 'Save and restart speaking to hear this engine'

// The settings winstond speaks with from the saved config, not the draft: which server, how to reach it.
const SERVER_FIELDS = ['provider', 'base_url', 'api_key_env', 'sample_rate'] as const

type Streaming = NonNullable<NonNullable<Config['tts']>['streaming']>

const same = (a: unknown, b: unknown) => deepEqual(a ?? null, b ?? null)

/** Why the running speech server is not the one the draft picks, if it is not. */
function engineDiffers(draft: Config, saved: Config | undefined, restartPending: boolean): string | undefined {
  if (restartPending || !saved) return NOT_RUNNING
  if (!same(draft.engines?.tts, saved.engines?.tts)) return NOT_RUNNING
  if (SERVER_FIELDS.some((k) => !same(draft.tts?.[k], saved.tts?.[k]))) return NOT_RUNNING
  return undefined
}

/** The draft's streaming with the schema's defaults where it has none, and what is wrong with it. */
function draftStreaming(page: SettingsState): { streaming: Streaming; problem?: string } {
  const streaming = page.value.tts?.streaming ?? {}
  const fields = section(page.schema, ['tts', 'streaming']).properties ?? {}
  const value = (key: 'first_secs' | 'lookahead_secs') => Number(streaming[key] ?? fields[key]?.default)
  // In the speech server's units, 25 tokens a second, rounded half to even as Python rounds.
  const tokens = (secs: number) => {
    const x = secs * 25
    const r = Math.abs(x - Math.trunc(x) - 0.5) < 1e-9 ? 2 * Math.round(x / 2) : Math.round(x)
    return Math.max(1, r)
  }
  const problem = tokens(value('lookahead_secs')) >= tokens(value('first_secs')) ? 'Look-ahead must be shorter than the first chunk' : undefined
  return { streaming, problem }
}

/** The Voice page's previews of unsaved settings, as SectionForm adornments on the tts section: a play
 * button beside Voice speaking one line in the picked voice, and "Play a sample" on the Streaming
 * heading speaking one long sentence with the draft's streaming, then its time to first sound. */
export function useVoicePreviews(page: SettingsState): Record<string, ReactNode> {
  const saved = useConfig().data
  const status = $api.useQuery('get', '/api/status').data
  const hello = usePreview()
  const sample = usePreview()
  const draft = page.value
  const tts = status?.engines.find((e) => e.name === 'tts')
  const pending = tts?.config_changed ?? false
  const blocked =
    draft.engines?.tts?.enabled === false
      ? 'Turn on speaking to hear a voice'
      : (engineDiffers(draft, saved, pending) ??
        (draft.engines?.tts && tts?.state !== 'healthy'
          ? tts?.state === 'installing'
            ? 'Speaking is still installing'
            : 'Speaking is not running yet'
          : undefined))
  const name = draft.assistant?.name || status?.assistant || ''
  const voice = draft.tts?.voice || undefined
  const { streaming, problem } = draftStreaming(page)
  const tag = JSON.stringify({ voice, streaming })
  const heard = sample.heard?.tag === tag ? sample.heard : null

  return {
    voice: (
      <PreviewButton
        label="Hear this voice"
        playing={hello.playing}
        blocked={blocked}
        onPlay={() => void hello.play({ text: `Hello, I'm ${name}. This is how I sound.`, voice })}
        onStop={hello.stop}
      />
    ),
    streaming: (
      <span className="flex items-center gap-2">
        {heard ? <span className="text-sm font-normal text-muted-foreground">{`${(heard.ms / 1000).toFixed(2)} s to first sound`}</span> : null}
        {heard?.streaming === 'ignored' ? <StatusTip icon={CircleAlert} className="text-warning" label={IGNORED} /> : null}
        <PreviewButton
          label="Play a sample"
          text
          playing={sample.playing}
          blocked={blocked ?? problem}
          onPlay={() => void sample.play({ text: SAMPLE, voice, streaming }, tag)}
          onStop={sample.stop}
        />
      </span>
    ),
  }
}
