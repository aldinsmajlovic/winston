import { Button } from '@/components/ui/button'

type Actions = { label?: string; busy: boolean; onClick?: () => void; onSkip?: () => void }

/** Skip for now, then the step's own button, when it has one yet. */
export function StepActions({ label, busy, onClick, onSkip }: Actions) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 border-t pt-4">
      {onSkip ? (
        <Button variant="ghost" disabled={busy} onClick={onSkip}>
          Skip for now
        </Button>
      ) : null}
      {label ? (
        <Button disabled={busy} onClick={onClick}>
          {label}
        </Button>
      ) : null}
    </div>
  )
}
