import { useCallback, useState, type ReactNode } from 'react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { buttonVariants } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ConfirmContext, type Ask, type ConfirmRequest } from '@/hooks/use-confirm'

type Pending = { request: ConfirmRequest; resolve: (value: string | null) => void }

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const [checked, setChecked] = useState(false)
  const ask = useCallback<Ask>((request) => new Promise((resolve) => {
    setChecked(request.check?.checked ?? false)
    setPending({ request, resolve })
  }), [])
  const settle = (value: string | null) => {
    pending?.request.check?.answer(checked)
    pending?.resolve(value)
    setPending(null)
  }
  const request = pending?.request
  return (
    <ConfirmContext value={ask}>
      {children}
      <AlertDialog open={pending !== null} onOpenChange={(open) => !open && settle(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{request?.title}</AlertDialogTitle>
            {request?.lines?.length ? (
              <AlertDialogDescription asChild>
                <ul>
                  {request.lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </AlertDialogDescription>
            ) : null}
          </AlertDialogHeader>
          {request?.check ? (
            <div className="flex min-w-0 items-start gap-3">
              <Checkbox id="confirm-check" className="mt-0.5" checked={checked} onCheckedChange={(on) => setChecked(on === true)} />
              <div className="flex min-w-0 flex-col gap-1">
                <label htmlFor="confirm-check" className="cursor-pointer text-sm">
                  {request.check.label}
                </label>
                {request.check.detail?.map((line, i) => (
                  <span key={i} className="truncate text-sm text-muted-foreground">
                    {line}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            {request?.choices.map((choice) => (
              <AlertDialogAction
                key={choice.value}
                className={choice.destructive ? buttonVariants({ variant: 'destructive' }) : undefined}
                onClick={() => settle(choice.value)}
              >
                {choice.label}
              </AlertDialogAction>
            ))}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ConfirmContext>
  )
}
