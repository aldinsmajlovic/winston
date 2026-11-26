import type { ComponentProps, MouseEvent } from 'react'
import { ExternalLink } from 'lucide-react'
import { $api, type ModelFacts } from '@/api/client'
import { useConfig } from '@/api/config'
import { ChoiceName, ChoicePicture, Combobox, type Choice, type RowActions } from '@/components/combobox'
import { KindIcon } from '@/components/kind-icon'
import { Badge } from '@/components/ui/badge'
import { useDraft } from '@/hooks/use-draft'
import { useGraphicsMemory } from '@/hooks/use-graphics-memory'
import { capitalize, size } from '@/lib/format'
import { at, gb, memoryUse, type Use } from '@/lib/graphics-memory'
import { cn } from '@/lib/utils'

type Fit = { mib: number; tone: Use['tone']; measured: boolean }

const TONE: Record<Use['tone'], string> = { ok: 'text-success', warn: 'text-warning', bad: 'text-destructive' }
const VERDICT: Record<Use['tone'], string> = { ok: 'fits', warn: 'only just fits', bad: 'does not fit' }
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
 * greyed, that Ollama pulls its own copy, that it is not downloaded yet. */
function extra(hint?: string | null): string {
  return (hint ?? '').split(', ').filter((p) => !/^\d[\d.]* (GB|MB)$|downloads$|sizes?$/.test(p)).join(', ')
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
    return use.brainHere ? { mib: at(f.points, k), tone: use.tone, measured: !!f.measured } : null
  }
  return { fit, plan, k }
}

/** One model: its name and source, the memory it takes and whether that fits, what it is, and the facts to
 * choose by. A GGUF repo's other sizes are a search away; Ollama's sizes are picked from the row. */
function ModelRow({ choice: c, act, fit }: { choice: Choice; act: RowActions; fit: Fit | null }) {
  const f = c.facts
  const more = extra(c.hint)
  if (!f) {
    return (
      <>
        <ChoicePicture choice={c} className="size-6" />
        <ChoiceName text={c.label} className="flex-1 truncate" />
        {c.hint ? <span className="max-w-1/3 truncate text-xs text-muted-foreground">{c.hint}</span> : null}
      </>
    )
  }
  const facts = [
    f.params && `${f.params} parameters`,
    f.bytes && `${size(f.bytes)} of weights`,
    f.context && `up to ${Math.round(f.context / 1024)}K tokens`,
    f.license,
    f.downloads != null && `${count.format(f.downloads)} downloads`,
    f.likes ? `${count.format(f.likes)} likes` : null,
    f.updated && `updated ${ago(f.updated)}`,
  ].filter((x): x is string => !!x)
  const ollama = f.source === 'Ollama'
  const name = c.value.split(':')[0]
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1 py-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="flex min-w-0 items-center gap-2">
          <KindIcon name={ollama ? 'ollama' : f.source ? 'huggingface' : null} />
          <ChoiceName text={c.label} className="truncate font-medium" />
        </span>
        {fit ? (
          <span className={cn('shrink-0 text-xs font-medium tabular-nums', TONE[fit.tone])}>
            {fit.measured ? '' : '≈ '}{gb(fit.mib)} GB, {VERDICT[fit.tone]}
          </span>
        ) : null}
      </div>
      {f.description || more ? (
        <p className="line-clamp-2 text-xs text-muted-foreground">{[f.description, more && capitalize(more)].filter(Boolean).join('. ')}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {facts.map((t) => <span key={t}>{t}</span>)}
        {f.tags?.map((t) => <Badge key={t} variant="secondary">{t}</Badge>)}
        {ollama && f.sizes?.length ? (
          <span className="flex flex-wrap items-center gap-1">
            Sizes
            {f.sizes.map((s) => (
              <button key={s} type="button" className="rounded-md border px-1.5 hover:bg-background hover:text-foreground"
                onClick={(e) => { stop(e); act.pick(`${name}:${s}`) }}>
                {s}
              </button>
            ))}
          </span>
        ) : null}
        {!ollama && f.repo && (f.sizes?.length ?? 0) > 1 ? (
          <button type="button" className="rounded-md border px-1.5 hover:bg-background hover:text-foreground"
            onClick={(e) => { stop(e); act.search(f.repo!) }}>
            {f.sizes!.length} sizes
          </button>
        ) : null}
        {f.link ? (
          <a href={f.link} target="_blank" rel="noreferrer" onClick={stop}
            className="inline-flex items-center gap-1 underline-offset-4 hover:text-foreground hover:underline">
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
  const where = plan?.memory === 'system' ? "the computer's" : plan?.gpu ? `the ${plan.gpu}'s` : "the card's"
  const memory = plan?.total_mb && props.choices.some((c) => fit(c.facts))
    ? `Memory is the brain's with ${k}K tokens in mind (≈ where estimated from its size). Green fits ${where} ` +
      `${gb(plan.total_mb)} GB beside his voice and hearing, amber only just, red does not.`
    : undefined
  return (
    <Combobox {...props} title="Model" description={memory}
      row={(c, act) => <ModelRow choice={c} act={act} fit={fit(c.facts)} />} />
  )
}
