import { useEffect, useState } from 'react'
import type { Presets } from '@/api/client'
import { Button } from '@/components/ui/button'
import { StatusLine } from '@/components/status-line'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { useGraphicsMemory } from '@/hooks/use-graphics-memory'
import type { SettingsState } from '@/hooks/use-settings'
import { exchanges, gb, memoryUse, type Part, type Use } from '@/lib/graphics-memory'
import { applyPreset, matchPreset, type Kind } from '@/lib/presets'
import { setIn } from '@/lib/schema'
import { cn } from '@/lib/utils'

// The parts in steps of ink, brain darkest, other programs palest; the verdict's colour is only in its sentence.
const SHADE: Record<Part['strength'], string> = {
  0: 'fill-foreground/15',
  1: 'fill-foreground/30',
  2: 'fill-foreground/45',
  3: 'fill-foreground/65',
  4: 'fill-foreground',
}
const W = 1000          // the bar's drawing width; it stretches to the card
const GAP = 3

// The bar and Free already say how full: the verdict is its tone's icon and one short sentence.
const TONE: Record<Use['tone'], 'ok' | 'warning' | 'error'> = { ok: 'ok', warn: 'warning', bad: 'error' }

function verdict(use: Use, system: boolean): [Use['tone'], string] {
  if (use.share > 1)
    return ['bad', system ? 'Does not fit: he slows to a crawl, or a program is stopped.'
      : use.spill === 'slow' ? 'Does not fit: part of the brain runs on the processor, about three times slower.' : 'Does not fit, so it will not start.']
  // measured in hardware.md, speed on the processor
  if (system && use.brainHere) return ['warn', 'Fits, but too slow to talk to: over a minute to his first word. A hosted brain answers in seconds.']
  if (use.share >= 0.9) return ['warn', 'Fits, only just: other programs may push it over.']
  if (use.share >= 0.8) return ['warn', 'Fits, with little room left for other programs.']
  return ['ok', 'Fits easily.']
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

/** The bar: the parts in steps of ink along the card, and where they do not fit, a dashed line where it ends.
 * The model picker draws one small per model (`decorative`, its row says the numbers). */
export function UseBar({ use, className, decorative }: { use: Use; className: string; decorative?: boolean }) {
  const scale = Math.max(use.total, use.used)
  const end = (use.total / scale) * W
  return (
    <svg viewBox={`0 0 ${W} 20`} preserveAspectRatio="none" className={cn('w-full overflow-hidden bg-muted', className)}
      {...(decorative ? { 'aria-hidden': true } : { role: 'img', 'aria-label': `${gb(use.used)} of ${gb(use.total)} GB` })}>
      {bars(use.parts, scale).map(({ p, x, w }) => (
        <rect key={p.label} x={x} y={0} width={w} height={20} className={SHADE[p.strength]}>
          <title>{`${p.label}: ${p.estimate ? '≈ ' : ''}${gb(p.mib)} GB`}</title>
        </rect>
      ))}
      {use.used > use.total ? (
        // on a halo of the background, so the card's end shows on the brain's ink too
        <>
          <line x1={end} x2={end} y1={0} y2={20} className="stroke-background" strokeWidth={4} vectorEffect="non-scaling-stroke" />
          <line x1={end} x2={end} y1={0} y2={20} className="stroke-foreground" strokeDasharray="3 2" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        </>
      ) : null}
    </svg>
  )
}

/** Whether the draft holds the picks recommended for this card. */
type Picks = { llm: string; model: string; tts?: string | null; stt: string; context: number }

function isRecommended(r: Picks, page: SettingsState, presets: Presets): boolean {
  const c = page.value
  return matchPreset('llm', c, presets.llm) === r.llm && c.llm.model === r.model && c.llm.context === r.context
    && matchPreset('tts', c, presets.tts) === r.tts && matchPreset('stt', c, presets.stt) === r.stt
}

/** How full the graphics card (or without one, the computer's memory) would be with the draft's picks, said
 * in green, amber or red. The card scrolls with the page; once it is out of view above, a slim strip of the
 * bar and its verdict pins to the top edge on the page's background, a rule under it, so the picks below can
 * change in sight of it (from tablet width up; on a phone a wrapped verdict would take too much of the screen).
 * In the setup wizard (recommend), also whether they are the picks recommended here, with a way back to them. */
export function MemoryBar({ page, presets, recommend }: { page: SettingsState; presets: Presets; recommend?: boolean }) {
  const plan = useGraphicsMemory(page.value)
  const [card, setCard] = useState<HTMLDivElement | null>(null)
  const [above, setAbove] = useState(false)
  useEffect(() => {
    if (!card) return
    const seen = new IntersectionObserver(([e]) => setAbove(!e.isIntersecting && e.boundingClientRect.top < 0))
    seen.observe(card)
    return () => seen.disconnect()
  }, [card])
  if (!plan?.total_mb) return null
  const system = plan.memory === 'system'
  const use = memoryUse(plan, page.value, presets)
  const [tone, says] = verdict(use, system)
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
    <>
      {above ? (
        // Takes no room in the column: no height, and its -mb-6 gives back the column's gap-6, so nothing moves
        // when it comes. A copy of what the card says, so hidden: the card's live region says it once.
        <div className="sticky top-0 z-10 -mb-6 h-0 max-sm:hidden" aria-hidden>
          <div className="flex animate-in flex-col gap-2 border-b bg-background py-3 duration-300 ease-enter fade-in">
            <UseBar use={use} className="h-2.5 rounded-sm" decorative />
            <StatusLine tone={TONE[tone]}>{says}</StatusLine>
          </div>
        </div>
      ) : null}
      <Card ref={setCard} aria-live="polite">
        <CardHeader className="flex flex-row flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <CardTitle>{system ? 'Computer memory' : 'Graphics memory'}</CardTitle>
          <span className="text-sm text-muted-foreground">
            {system ? '' : `${plan.gpu}, `}{gb(use.total)} GB
          </span>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <UseBar use={use} className="h-5 rounded-md" />
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
            {use.parts.map((p) => (
              <span key={p.label} className="flex items-center gap-1.5">
                <svg viewBox="0 0 10 10" className="size-2.5" aria-hidden>
                  <rect width={10} height={10} rx={2} className={SHADE[p.strength]} />
                </svg>
                {p.label} {p.estimate ? '≈ ' : ''}{gb(p.mib)} GB
              </span>
            ))}
            {use.used < use.total ? (
              <span className="flex items-center gap-1.5">
                <svg viewBox="0 0 10 10" className="size-2.5" aria-hidden>
                  <rect x={0.5} y={0.5} width={9} height={9} rx={2} className="fill-muted stroke-muted-foreground/50" />
                </svg>
                Free {gb(use.total - use.used)} GB
              </span>
            ) : null}
          </div>
          <StatusLine tone={TONE[tone]}>{says}</StatusLine>
          {system && !use.brainHere && r ? (
            <p className="text-sm text-muted-foreground">No graphics card, so a hosted brain is recommended: one on the processor is too slow to talk to.</p>
          ) : null}
          {use.otherUnknown ? (
            <p className="text-sm text-muted-foreground">Other programs are left out: this card does not say what each program holds while his engines run.</p>
          ) : null}
          {use.brainUnknown ? (
            <p className="text-sm text-muted-foreground">The brain's share is not known for this model, so it is left out.</p>
          ) : null}
          {r ? (
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t pt-3 text-sm text-muted-foreground">
              {isRecommended(r, page, presets) ? (
                <span>These are the picks recommended for his computer.</span>
              ) : (
                <>
                  <span>Changed from the picks recommended for his computer.</span>
                  <Button variant="outline" size="sm" onClick={back}>
                    Use the recommendation
                  </Button>
                </>
              )}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </>
  )
}

/** How much one conversation holds (llm.context), for a brain he runs here: a slider in steps, with about how
 * many exchanges that is and what it costs on the card, which the memory bar shows as it moves. */
export function ContextSlider({ page, presets }: { page: SettingsState; presets: Presets }) {
  const plan = useGraphicsMemory(page.value)
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
