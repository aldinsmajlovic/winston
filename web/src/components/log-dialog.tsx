import { LoaderCircle } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type Props = { title: string; open: boolean; onOpenChange: (open: boolean) => void; lines?: string[]; loading?: boolean }

/** The last lines of a log ("Log of Spotify"); the page fetches them while it is open. */
export function LogDialog({ title, open, onOpenChange, lines, loading }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-auto rounded-md border p-3 text-xs/relaxed whitespace-pre tabular-nums">
          {loading ? (
            <LoaderCircle className="size-4 animate-spin text-muted-foreground" aria-label="Loading" />
          ) : lines?.length ? (
            lines.join('\n')
          ) : (
            'Nothing logged yet.'
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
