import { useState, type FormEvent } from 'react'
import { Flag } from 'lucide-react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { $api, errorText, type TurnMark } from '@/api/client'
import { MARK_LABEL, markText, markedKey, type MarkKind } from '@/lib/marks'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

const OWN_ERRORS = { meta: { showsErrors: true } }

type Props = { turnId: string; heard: string; mark?: TurnMark | null; className?: string }

/** A flag beside a turn you said: mark it as one of his mistakes (core/turns.py), or take the mark off. Until
 * it is marked it shows on the line's hover, and always on a touch screen. Misheard asks what you actually said. */
export function TurnMarkControl({ turnId, heard, mark, className }: Props) {
  const client = useQueryClient()
  const put = $api.useMutation('put', '/api/turns/{turn_id}/mark', OWN_ERRORS)
  const remove = $api.useMutation('delete', '/api/turns/{turn_id}/mark', OWN_ERRORS)
  const [asking, setAsking] = useState(false)
  const [said, setSaid] = useState(heard)
  const path = { params: { path: { turn_id: turnId } } }

  const refresh = () => {
    void client.invalidateQueries({ queryKey: markedKey })
    void client.invalidateQueries({ queryKey: ['get', '/api/turns'] })
  }
  const unmark = (after?: () => void) =>
    remove.mutate(path, { onSuccess: () => (refresh(), after?.()), onError: (e) => toast.error(errorText(e)) })
  const markAs = (kind: MarkKind, saidText?: string) =>
    put.mutate(
      { ...path, body: { kind, said: saidText || null } },
      {
        onError: (e) => toast.error(errorText(e)),
        onSuccess: () => {
          refresh()
          toast(`Marked: ${MARK_LABEL[kind]}`, { action: { label: 'Undo', onClick: () => unmark() } })
        },
      },
    )
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setAsking(false)
    markAs('misheard', said.trim() === heard.trim() ? undefined : said.trim())
  }

  return (
    <>
      <DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={mark ? `Marked: ${MARK_LABEL[mark.kind]}` : 'Mark a mistake'}
                className={cn(
                  !mark &&
                    'text-muted-foreground opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100 pointer-coarse:opacity-100',
                  className,
                )}
              >
                <Flag className={cn(mark && 'fill-current')} />
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent>{mark ? markText(mark) : 'Mark a mistake'}</TooltipContent>
        </Tooltip>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuLabel className="flex flex-col gap-0.5 text-sm text-foreground">
            Mark a mistake
            <span className="font-normal text-muted-foreground">
              Kept so later changes can be tested on it. He does not learn from it by himself.
            </span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              setSaid(mark?.said ?? heard)
              setAsking(true)
            }}
          >
            {MARK_LABEL.misheard}
          </DropdownMenuItem>
          {(['not_addressed', 'wrong_action', 'too_slow'] as const).map((kind) => (
            <DropdownMenuItem key={kind} onSelect={() => markAs(kind)}>
              {MARK_LABEL[kind]}
            </DropdownMenuItem>
          ))}
          {mark ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => unmark()}>Remove mark</DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={asking} onOpenChange={setAsking}>
        <DialogContent>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <DialogHeader>
              <DialogTitle>What you said</DialogTitle>
            </DialogHeader>
            <Input autoFocus aria-label="What you said" value={said} onChange={(e) => setSaid(e.target.value)} />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setAsking(false)}>
                Cancel
              </Button>
              <Button type="submit">Mark</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
