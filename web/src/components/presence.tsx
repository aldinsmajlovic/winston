import { useEffect, useId, useRef } from 'react'
import { MARK, REST, ease, paths, target, type Frame, type MarkState } from '@/lib/mark'
import { cn } from '@/lib/utils'

const AT_REST = paths(REST)
const RIM = MARK.radius - 1.5 * MARK.ring

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
  const glow = useRef<SVGRectElement>(null)
  const live = useRef({ state, since: 0, frame: REST, raf: 0 })

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
      glow.current?.setAttribute('opacity', run.frame.glow.toFixed(3))
      fields.current?.setAttribute('opacity', run.frame.level.toFixed(3))
      run.raf = run.state === 'idle' && settled(run.frame, REST) ? 0 : requestAnimationFrame(draw)
    }
    if (!run.raf) run.raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(run.raf)
      run.raf = 0
    }
  }, [state])

  return (
    <svg viewBox="0 0 100 100" role="img" aria-label={`Winston is ${state}`} className={cn('shrink-0', className)}>
      <defs>
        <clipPath id={`${id}-disc`}>
          <circle cx="50" cy="50" r={MARK.radius} />
        </clipPath>
        <mask id={`${id}-rim`}>
          <rect width="100" height="100" fill="white" />
          <circle cx="50" cy="50" r={RIM} fill="none" stroke="black" strokeWidth={MARK.ring} />
        </mask>
        <linearGradient id={`${id}-sky`} gradientUnits="userSpaceOnUse" x1="50" y1="4" x2="50" y2="96">
          <stop offset="0" stopColor="currentColor" className="text-mark-sky-high" />
          <stop offset="0.6" stopColor="currentColor" className="text-mark-sky-low" />
        </linearGradient>
        <linearGradient id={`${id}-coal`} gradientUnits="userSpaceOnUse" x1="50" y1="4" x2="50" y2="96">
          <stop offset="0.4" stopColor="currentColor" className="text-mark-coal-high" />
          <stop offset="1" stopColor="currentColor" className="text-mark-coal-low" />
        </linearGradient>
      </defs>
      <g mask={`url(#${id}-rim)`}>
        <g ref={fields} clipPath={`url(#${id}-disc)`} opacity={REST.level}>
          <rect width="100" height="100" fill={`url(#${id}-coal)`} />
          <rect ref={glow} width="100" height="100" className="fill-white" opacity="0" />
          <path ref={upper} d={AT_REST.upper} fill={`url(#${id}-sky)`} />
          <path
            ref={glint}
            d={AT_REST.line}
            fill="none"
            className="stroke-white"
            strokeWidth="5"
            strokeLinecap="round"
            pathLength={100}
            strokeDasharray="18 82"
            opacity="0"
          />
        </g>
      </g>
    </svg>
  )
}
