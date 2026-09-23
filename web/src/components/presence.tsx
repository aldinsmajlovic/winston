import type { PresenceState } from '@/api/live'
import { cn } from '@/lib/utils'

const DOTS = 12

/** Winston's presence. A placeholder ring of dots until the logo exists (docs/identity.md); every
 * page draws him through this component, so the logo replaces this file only. */
export function Presence({ state, className }: { state: PresenceState; className?: string }) {
  return (
    <svg viewBox="0 0 100 100" role="img" aria-label={`Winston is ${state}`} className={cn('shrink-0 rounded-full bg-card', className)}>
      {Array.from({ length: DOTS }, (_, i) => {
        const angle = ((i * 360) / DOTS - 90) * (Math.PI / 180)
        return (
          <circle
            key={i}
            cx={50 + 34 * Math.cos(angle)}
            cy={50 + 34 * Math.sin(angle)}
            r={5.5}
            className={state === 'idle' ? 'fill-muted-foreground/30' : 'fill-primary'}
          >
            {state === 'thinking' && (
              <animate attributeName="opacity" values="1;0.15;0.15" keyTimes="0;0.4;1" dur="1.2s" begin={`${i * 0.1}s`} repeatCount="indefinite" />
            )}
            {state === 'speaking' && (
              <animate attributeName="opacity" values="0.35;1;0.35" dur="0.9s" begin={`${(((i * 5) % DOTS) / DOTS) * 0.9}s`} repeatCount="indefinite" />
            )}
          </circle>
        )
      })}
    </svg>
  )
}
