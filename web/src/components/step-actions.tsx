import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router'
import { $api } from '@/api/client'
import { Button } from '@/components/ui/button'

type Actions = { label?: string; busy: boolean; onClick?: () => void; onSkip?: () => void }

/** Back to the step before (not on the first, nor on a step picked up after setup ended), then Skip for
 * now and the step's own button, when it has one yet. */
export function StepActions({ label, busy, onClick, onSkip }: Actions) {
  const { step } = useParams()
  const setup = $api.useQuery('get', '/api/setup').data
  const order = setup?.active ? [...setup.order, 'done'] : []
  const back = order[order.indexOf(step ?? '') - 1]
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 border-t pt-4">
      {back ? (
        <Button asChild variant="ghost" className="mr-auto">
          <Link to={`/setup/${back}`}>
            <ArrowLeft data-icon="inline-start" />
            Back
          </Link>
        </Button>
      ) : null}
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
