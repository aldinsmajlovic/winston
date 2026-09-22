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
import { ConfirmContext, type Ask, type ConfirmRequest } from '@/hooks/use-confirm'

type Pending = { request: ConfirmRequest; resolve: (value: string | null) => void }

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const ask = useCallback<Ask>((request) => new Promise((resolve) => setPending({ request, resolve })), [])
  const settle = (value: string | null) => {
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
