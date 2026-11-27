import type { ComponentProps, MouseEvent } from 'react'
import { ExternalLink } from 'lucide-react'
import { $api, type ModelFacts } from '@/api/client'
import { useConfig } from '@/api/config'
import { ChoiceName, ChoicePicture, Combobox, type Choice, type Filter, type RowActions } from '@/components/combobox'
import { KindIcon } from '@/components/kind-icon'
import { UseBar } from '@/components/memory-bar'
import { Button } from '@/components/ui/button'
import { useDraft } from '@/hooks/use-draft'
import { useGraphicsMemory } from '@/hooks/use-graphics-memory'
import { capitalize, size } from '@/lib/format'
import { at, gb, memoryUse, type Use } from '@/lib/graphics-memory'
import { cn } from '@/lib/utils'

type Fit = { mib: number; tone: Use['tone']; measured: boolean; use: Use }

// Quiet when it fits; the verdict's colour only on a model that does not, its words for a screen reader.
const TONE: Record<Use['tone'], string> = { ok: '', warn: 'text-warning', bad: 'text-destructive' }
const VERDICT: Record<Use['tone'], string> = { ok: 'fits', warn: 'only just fits', bad: 'does not fit' }
// The Hub's and Ollama's tags in the app's words (an action, not a tool).
const TAGS: Record<string, string> = { vision: 'sees images', tools: 'uses actions', thinking: 'can think first', MoE: 'mixture of experts' }
const count = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/** "last month", "3 years ago". */
function ago(iso: string): string {
  const days = (Date.now() - Date.parse(iso)) / 864e5
  if (days < 30) return relative.format(-Math.round(days), 'day')
  if (days < 365) return relative.format(-Math.round(days / 30), 'month')
  return relative.format(-Math.round(days / 365), 'year')
}

/** What a choice's hint says beyond the facts the row shows (its size, downloads, how many sizes): why it is
 * greyed, that Ollama pulls its own copy, that it is not downloaded yet. Sentence case, but vLLM stays vLLM. */
function extra(hint?: string | null): string {
  const s = (hint ?? '').split(', ').filter((p) => !/^\d[\d.]* (GB|MB)$|downloads$|sizes?$/.test(p)).join(', ')
  return /^[a-z][a-z ]/.test(s) ? capitalize(s) : s
}

/** "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL" as its name, its size and who made it. */
function parts(label: string): { name: string; tag: string | null; maker: string | null } {
  const i = label.lastIndexOf(':')
  const path = (i > 0 ? label.slice(0, i) : label).replace(/^hf\.co\//, '')
  const j = path.lastIndexOf('/')
  return { name: path.slice(j + 1), tag: i > 0 ? label.slice(i + 1) : null, maker: j > 0 ? path.slice(0, j) : null }
}

const stop = (e: MouseEvent) => e.stopPropagation()

/** How each model would sit in memory if picked, with the draft's context, voice and hearing: what the memory
 * bar would then say. None where the bar does not count the brain (hosted) or the model's memory is not known
 * (vLLM takes its share of the card whatever the model). */
function useModelFit() {
  const config = useConfig()
  const { draft } = useDraft()
  const cfg = draft ?? config.data
  const plan = useGraphicsMemory(cfg)
  const presets = $api.useQuery('get', '/api/presets')
  const k = (cfg?.llm?.context ?? 16384) / 1024
  const fit = (f?: ModelFacts | null): Fit | null => {
    if (!plan?.total_mb || !cfg || !presets.data || !f?.points) return null
    const use = memoryUse({ ...plan, brain: { points: f.points, measured: !!f.measured, share: null } }, cfg, presets.data)
    return use.brainHere ? { mib: at(f.points, k), tone: use.tone, measured: !!f.measured, use } : null
  }
  return { fit, plan, k }
}

/** The memory bar as it would be with this model, and the brain's share of it. */
function Memory({ fit }: { fit: Fit }) {
  return (
    <span className="flex shrink-0 items-center gap-2 pt-0.5">
      {/* important: a command item sizes every icon in it to 16 px */}
      <UseBar use={fit.use} className="h-1.5! w-16! rounded-full sm:w-24!" decorative />
      <span className={cn('w-17 text-right text-xs font-medium tabular-nums', TONE[fit.tone])}>
        {fit.measured ? '' : '≈ '}{gb(fit.mib)} GB<span className="sr-only">, {VERDICT[fit.tone]}</span>
      </span>
    </span>
  )
}

/** One model: its name and size, the memory it would take, what it is, and the few facts to choose by. A GGUF
 * repo's other sizes are a search away; Ollama's are picked from the row. */
function ModelRow({ choice: c, act, fit }: { choice: Choice; act: RowActions; fit: Fit | null }) {
  const f = c.facts
  if (!f) {
    return (
      <>
        <ChoicePicture choice={c} className="size-6" />
        <ChoiceName text={c.label} className="flex-1 truncate" />
        {c.hint ? <span className="max-w-1/3 truncate text-xs text-muted-foreground">{c.hint}</span> : null}
      </>
    )
  }
  const { name, tag, maker } = parts(c.label)
  const about = maker && f.description ? `${maker}, ${f.description.replace(/^Made from/, 'made from')}` : maker ?? f.description
  const facts = [
    extra(c.hint),
    f.params && `${f.params} parameters`,
    !fit && f.bytes && `${size(f.bytes)} of weights`,
    f.context && `${Math.round(f.context / 1024)}K tokens`,
    ...(f.tags ?? []).map((t) => TAGS[t] ?? t),
    f.downloads != null && `${count.format(f.downloads)} downloads`,
    f.updated && `updated ${ago(f.updated)}`,
  ].filter((x): x is string => !!x)
  const ollama = f.source === 'Ollama'
  const base = c.value.split(':')[0]
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1 py-1.5">
      <div className="flex items-start gap-3">
        <span className="flex min-w-0 flex-1 gap-2">
          <span className="pt-0.5">
            <KindIcon name={ollama ? 'ollama' : f.source ? 'huggingface' : null} />
          </span>
          <span className="min-w-0 break-words">
            <span className="font-medium">{name}</span>
            {tag ? <span className="text-muted-foreground"> {tag}</span> : null}
          </span>
        </span>
        {fit ? <Memory fit={fit} /> : null}
      </div>
      {about ? <p className="line-clamp-2 text-xs text-muted-foreground">{about}</p> : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {facts.map((t) => <span key={t}>{t}</span>)}
        {ollama && f.sizes?.length ? (
          <span className="flex flex-wrap items-center gap-1">
            Sizes
            {f.sizes.map((s) => (
              <Button key={s} type="button" variant="outline" size="sm" onClick={(e) => { stop(e); act.pick(`${base}:${s}`) }}>
                {s}
              </Button>
            ))}
          </span>
        ) : null}
        {!ollama && f.repo && (f.sizes?.length ?? 0) > 1 ? (
          <Button type="button" variant="outline" size="sm" onClick={(e) => { stop(e); act.search(f.repo!) }}>
            {f.sizes!.length} sizes
          </Button>
        ) : null}
        {f.link ? (
          <a href={f.link} target="_blank" rel="noreferrer" onClick={stop}
            className="inline-flex items-center gap-1 text-foreground underline underline-offset-4">
            {f.source} <ExternalLink className="size-3!" aria-hidden />
          </a>
        ) : null}
      </div>
    </div>
  )
}

/** The brain's Model: the searchable list in a dialog of rows with what each model is and whether it fits. */
export function ModelPicker(props: ComponentProps<typeof Combobox>) {
  const { fit, plan, k } = useModelFit()
  const fits = plan?.total_mb ? props.choices.some((c) => fit(c.facts)) : false
  const bar = plan?.memory === 'system' ? 'computer memory' : 'graphics memory'
  const filters: Filter[] = [
    ...(fits ? [{ label: 'Fits', keep: (c: Choice) => (fit(c.facts)?.tone ?? 'bad') !== 'bad' }] : []),
    ...(props.choices.some((c) => c.facts?.tags?.includes('vision')) ? [{ label: 'Sees images', keep: (c: Choice) => !!c.facts?.tags?.includes('vision') }] : []),
  ]
  return (
    <Combobox {...props} title="Model" filters={filters}
      description={fits ? `Each bar is the ${bar} bar with that model and ${k}K tokens in mind.` : undefined}
      row={(c, act) => <ModelRow choice={c} act={act} fit={fit(c.facts)} />} />
  )
}
