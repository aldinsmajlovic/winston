import type { Presets } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { useGraphicsMemory } from '@/hooks/use-graphics-memory'
import type { SettingsState } from '@/hooks/use-settings'
import { exchanges, gb, memoryUse, type Part, type Use } from '@/lib/graphics-memory'
import { applyPreset, matchPreset, type Kind } from '@/lib/presets'
import { setIn } from '@/lib/schema'
import { cn } from '@/lib/utils'

const FILL: Record<Use['tone'], string> = { ok: 'fill-success', warn: 'fill-warning', bad: 'fill-destructive' }
const TEXT: Record<Use['tone'], string> = { ok: 'text-success', warn: 'text-warning', bad: 'text-destructive' }
// How strongly each part takes the colour, brain first; 0 is other programs, in grey.
const SHADE: Record<Part['strength'], string> = { 0: '', 1: 'opacity-25', 2: 'opacity-40', 3: 'opacity-60', 4: '' }
const W = 1000          // the bar's drawing width; it stretches to the card
const GAP = 3

function shade(p: Part, tone: Use['tone']): string {
  return p.strength ? cn(FILL[tone], SHADE[p.strength]) : 'fill-muted-foreground/45'
}

function verdict(use: Use, system: boolean): [string, string] {
  const pct = use.share * 100
  if (pct > 100)
    return ['Does not fit.', system ? 'The computer would run out of memory: he slows to a crawl, or a program is stopped.'
      : use.spill === 'slow' ? 'Part of the brain would run on the processor, about three times slower.' : 'What does not fit will not start.']
  if (pct >= 90) return ['Fits, only just.', system ? 'Other programs may push it over.' : 'Other programs that use the graphics card may push it over.']
  if (pct >= 80) return ['Fits.', system ? 'Little room left for other programs.' : 'Little room left for other programs on the graphics card.']
  return ['Fits easily.', `${gb(use.total - use.used)} GB to spare.`]
}

/** Each part's place on the bar, a small gap between them. */
function bars(parts: Part[], scale: number): { p: Part; x: number; w: number }[] {
  let x = 0
  return parts.map((p) => {
    const w = (p.mib / scale) * W
    const out = { p, x: x + (x ? GAP / 2 : 0), w: Math.max(0, w - (x ? GAP / 2 : 0) - GAP / 2) }
    x += w
    return out
  })
}

/** Whether the draft holds the picks recommended for this card. */
type Picks = { llm: string; model: string; tts?: string | null; stt: string; context: number }

function isRecommended(r: Picks, page: SettingsState, presets: Presets): boolean {
  const c = page.value
  return matchPreset('llm', c, presets.llm) === r.llm && c.llm.model === r.model && c.llm.context === r.context
    && matchPreset('tts', c, presets.tts) === r.tts && matchPreset('stt', c, presets.stt) === r.stt
}

/** How full the graphics card (or without one, the computer's memory) would be with the draft's picks, in
 * green, amber or red, kept in view while the picks below change (pinned from tablet width up; on a phone it
 * would cover a third of the screen). In the setup wizard (recommend), also whether they are the picks
 * recommended here, with a way back to them. */
export function MemoryBar({ page, presets, recommend }: { page: SettingsState; presets: Presets; recommend?: boolean }) {
  const plan = useGraphicsMemory(page)
  if (!plan?.total_mb) return null
  const system = plan.memory === 'system'
  const use = memoryUse(plan, page.value, presets)
  const scale = Math.max(use.total, use.used)
  const [head, more] = verdict(use, system)
  const r = recommend ? plan.recommended : null
  const back = () =>
    page.update((c) => {
      if (!r) return c
      let x = c
      for (const [kind, id] of [['llm', r.llm], ['tts', r.tts], ['stt', r.stt]] as [Kind, string | null][]) {
        const p = presets[kind].find((q) => q.id === id)
        if (p) x = applyPreset(x, kind, p)
      }
      return setIn(setIn(x, ['llm', 'model'], r.model), ['llm', 'context'], r.context)
    })
  return (
    <Card className="z-10 shadow-sm sm:sticky sm:top-2" aria-live="polite">
      <CardHeader className="flex flex-row flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <CardTitle>{system ? 'Memory' : 'Graphics memory'}</CardTitle>
        <span className="text-sm text-muted-foreground">
          {system ? 'No graphics card, so the brain runs on the processor' : plan.gpu}, {gb(use.total)} GB
        </span>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <svg viewBox={`0 0 ${W} 20`} preserveAspectRatio="none" className="h-5 w-full overflow-hidden rounded-md bg-muted" role="img" aria-label={`${gb(use.used)} of ${gb(use.total)} GB`}>
          {bars(use.parts, scale).map(({ p, x, w }) => (
            <rect key={p.label} x={x} y={0} width={w} height={20} className={shade(p, use.tone)}>
              <title>{`${p.label}: ${gb(p.mib)} GB`}</title>
            </rect>
          ))}
          {use.used > use.total ? <line x1={(use.total / scale) * W} x2={(use.total / scale) * W} y1={0} y2={20} className="stroke-foreground" strokeDasharray="3 2" strokeWidth={2} /> : null}
        </svg>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
          {use.parts.map((p) => (
            <span key={p.label} className="flex items-center gap-1.5">
              <svg viewBox="0 0 10 10" className="size-2.5" aria-hidden>
                <rect width={10} height={10} rx={2} className={shade(p, use.tone)} />
              </svg>
              {p.label} {gb(p.mib)} GB
            </span>
          ))}
          <span>Free {gb(Math.max(0, use.total - use.used))} GB</span>
        </div>
        <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className={cn('font-medium', TEXT[use.tone])}>
            {gb(use.used)} of {gb(use.total)} GB, {Math.round(use.share * 100)}%. {head}
          </span>
          <span className="text-muted-foreground">{more}</span>
        </p>
        {use.otherUnknown ? (
          <p className="text-sm text-muted-foreground">Other programs are left out: this card does not say what each program holds while his engines run.</p>
        ) : null}
        {use.brainUnknown ? (
          <p className="text-sm text-muted-foreground">The brain's memory is not known for this model, so it is left out.</p>
        ) : use.brainHere && !use.brainMeasured ? (
          <p className="text-sm text-muted-foreground">The brain's share is estimated from the size of its model.</p>
        ) : null}
        {r ? (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t pt-3 text-sm text-muted-foreground">
            {isRecommended(r, page, presets) ? (
              <span>These are the picks recommended for this computer.</span>
            ) : (
              <>
                <span>Changed from the picks recommended for this computer.</span>
                <Button variant="outline" size="sm" onClick={back}>
                  Use the recommendation
                </Button>
              </>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

/** How much one conversation holds (llm.context), for a brain he runs here: a slider in steps, with about how
 * many exchanges that is and what it costs on the card, which the memory bar shows as it moves. */
export function ContextSlider({ page, presets }: { page: SettingsState; presets: Presets }) {
  const plan = useGraphicsMemory(page)
  if (!plan) return null
  const use = memoryUse(plan, page.value, presets)
  if (!use.brainHere) return null
  const tokens = page.value.llm.context ?? plan.min_k * 1024
  const k = tokens / 1024
  const extra = (use.brainAt(k) ?? 0) - (use.brainAt(plan.min_k) ?? 0)
  const cost = !plan.total_mb || use.brainUnknown ? ''
    : plan.brain.share ? ' vLLM holds the same share of the card at any length.'
    : k > plan.min_k ? ` Takes ${gb(extra)} GB more than ${plan.min_k}K.`
    : ' The least he needs: his instructions and tools take about 10.5K.'
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor="llm-context">How much he keeps in mind</Label>
        <span className="text-sm tabular-nums">{k}K tokens</span>
      </div>
      <Slider
        id="llm-context"
        min={plan.min_k}
        max={plan.max_k}
        step={plan.step_k}
        value={[Math.min(Math.max(k, plan.min_k), plan.max_k)]}
        onValueChange={([v]) => page.update((c) => setIn(c, ['llm', 'context'], v * 1024))}
        aria-label="How much he keeps in mind, in thousands of tokens"
      />
      <div className="flex justify-between text-xs text-muted-foreground tabular-nums">
        {/* the ends only: the slider is linear, so evenly spaced middle labels would sit at the wrong values */}
        {[plan.min_k, plan.max_k].map((t) => (
          <span key={t}>{t}K</span>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        Room for about {exchanges(tokens)} exchanges before he starts a fresh conversation; fewer when he reads the news or searches.{cost}
      </p>
    </div>
  )
}
