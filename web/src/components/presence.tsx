import { useEffect, useId, useRef } from 'react'
import { $api } from '@/api/client'
import { STATE_LABEL } from '@/api/live'
import { MARK, REST, ease, paths, target, type Frame, type MarkState } from '@/lib/mark'
import { cn } from '@/lib/utils'

const AT_REST = paths(REST)
const INNER = MARK.radius - 2 * MARK.ring
const BAND = MARK.radius - MARK.ring / 2
const LABEL: Record<MarkState, string> = { ...STATE_LABEL, announcing: 'Announcing' }

const settled = (a: Frame, b: Frame) =>
  (Object.keys(a) as (keyof Frame)[]).every((key) => Math.abs(a[key] - b[key]) < 0.002)

/** Winston's mark, moving with his state (docs/identity.md). Every page draws him through this
 * component. The shape is drawn once and its attributes are updated per frame, so React does not
 * re-render while it moves; the loop stops once he is idle and still. */
export function Presence({ state, className }: { state: MarkState; className?: string }) {
  const id = useId().replace(/[^\w-]/g, '')
  const fields = useRef<SVGGElement>(null)
  const upper = useRef<SVGPathElement>(null)
  const glint = useRef<SVGPathElement>(null)
  const lower = useRef<SVGCircleElement>(null)
  const live = useRef({ state, since: 0, frame: REST, raf: 0 })
  const name = $api.useQuery('get', '/api/status').data?.assistant ?? ''

  useEffect(() => {
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches
    const run = live.current
    run.state = state
    run.since = performance.now() / 1000
    const draw = (now: number) => {
      const goal = target(run.state, now / 1000 - run.since, still)
      run.frame = ease(run.frame, goal, still ? 1 : 0.14)
      const d = paths(run.frame)
      upper.current?.setAttribute('d', d.upper)
      glint.current?.setAttribute('d', d.line)
      glint.current?.setAttribute('stroke-dashoffset', (18 - run.frame.pos * 118).toFixed(2))
      glint.current?.setAttribute('opacity', (0.9 * run.frame.glint).toFixed(3))
      lower.current?.setAttribute('opacity', (1 - run.frame.glow).toFixed(3))
      fields.current?.setAttribute('opacity', run.frame.level.toFixed(3))
      run.raf = run.state === 'idle' && settled(run.frame, REST) ? 0 : requestAnimationFrame(draw)
    }
    if (!run.raf) run.raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(run.raf)
      run.raf = 0
    }
  }, [state])

  // One colour, the page's text: a ring, and inside it a disc filled below the W and open above it.
  // The running light is a gap cut along the W; the speaking glow fades the fill.
  return (
    <svg viewBox="0 0 100 100" role="img" aria-label={`${name} is ${LABEL[state].toLowerCase()}`} className={cn('shrink-0 text-foreground', className)}>
      <defs>
        <mask id={`${id}-open`}>
          <rect width="100" height="100" fill="white" />
          <path ref={upper} d={AT_REST.upper} fill="black" />
          <path
            ref={glint}
            d={AT_REST.line}
            fill="none"
            stroke="black"
            strokeWidth={MARK.ring}
            strokeLinecap="round"
            pathLength={100}
            strokeDasharray="18 82"
            opacity="0"
          />
        </mask>
      </defs>
      <g ref={fields} opacity={REST.level}>
        <circle ref={lower} cx="50" cy="50" r={INNER} fill="currentColor" mask={`url(#${id}-open)`} />
        <circle cx="50" cy="50" r={BAND} fill="none" stroke="currentColor" strokeWidth={MARK.ring} />
      </g>
    </svg>
  )
}
