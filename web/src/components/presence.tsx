import { useEffect, useId, useRef } from 'react'
import { $api } from '@/api/client'
import { STATE_LABEL, voice } from '@/api/live'
import { MARK, REST, ease, paths, target, type Frame, type MarkState } from '@/lib/mark'
import { cn } from '@/lib/utils'

const AT_REST = paths(REST)
const INNER = MARK.radius - 2 * MARK.ring
const BAND = MARK.radius - MARK.ring / 2
const LABEL: Record<MarkState, string> = { ...STATE_LABEL, announcing: 'Announcing' }

// Phase and position keep their last value and show nothing once the wave and arc are gone.
const settled = (a: Frame, b: Frame) =>
  (Object.keys(a) as (keyof Frame)[]).every((key) => key === 'phase' || key === 'pos' || Math.abs(a[key] - b[key]) < 0.002)

/** Winston's mark, moving with his state (docs/identity.md). Every page draws him through this
 * component. The shape is drawn once and its attributes are updated per frame, so React does not
 * re-render while it moves; the loop stops once he is idle and still. */
export function Presence({ state, className }: { state: MarkState; className?: string }) {
  const id = useId().replace(/[^\w-]/g, '')
  const fields = useRef<SVGGElement>(null)
  const upper = useRef<SVGPathElement>(null)
  const arc = useRef<SVGCircleElement>(null)
  const live = useRef({ state, since: 0, frame: REST, raf: 0, loud: 0 })
  const name = $api.useQuery('get', '/api/status').data?.assistant ?? ''

  useEffect(() => {
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches
    const run = live.current
    run.state = state
    run.since = performance.now() / 1000
    const draw = (now: number) => {
      // His voice as a meter shows it: up at once on a louder syllable, down slowly after. A level
      // that stopped coming is silence.
      const heard = now - voice.at < 250 ? voice.level : 0
      run.loud += (heard - run.loud) * (heard > run.loud ? 0.35 : 0.07)
      const goal = target(run.state, now / 1000 - run.since, still, run.loud)
      run.frame = ease(run.frame, goal, still ? 1 : 0.14)
      const d = paths(run.frame)
      upper.current?.setAttribute('d', d.upper)
      arc.current?.setAttribute('stroke-dashoffset', (-run.frame.pos * 100).toFixed(2))
      arc.current?.setAttribute('opacity', run.frame.arc.toFixed(3))
      fields.current?.setAttribute('opacity', run.frame.level.toFixed(3))
      run.raf = run.state === 'idle' && settled(run.frame, REST) ? 0 : requestAnimationFrame(draw)
    }
    if (!run.raf) run.raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(run.raf)
      run.raf = 0
    }
  }, [state])

  // One colour, his accent (petrol): a ring, and inside it a disc filled below the W and open above it.
  // The thinking arc is the ring drawn again at full strength over the dimmed one.
  return (
    <svg viewBox="0 0 100 100" role="img" aria-label={`${name} is ${LABEL[state].toLowerCase()}`} className={cn('shrink-0 text-primary', className)}>
      <defs>
        <mask id={`${id}-open`}>
          <rect width="100" height="100" fill="white" />
          <path ref={upper} d={AT_REST.upper} fill="black" />
        </mask>
      </defs>
      <g ref={fields} opacity={REST.level}>
        <circle cx="50" cy="50" r={INNER} fill="currentColor" mask={`url(#${id}-open)`} />
        <circle cx="50" cy="50" r={BAND} fill="none" stroke="currentColor" strokeWidth={MARK.ring} />
      </g>
      <circle
        ref={arc}
        cx="50"
        cy="50"
        r={BAND}
        fill="none"
        stroke="currentColor"
        strokeWidth={MARK.ring}
        strokeLinecap="round"
        pathLength={100}
        strokeDasharray="22 78"
        transform="rotate(-90 50 50)"
        opacity="0"
      />
    </svg>
  )
}
