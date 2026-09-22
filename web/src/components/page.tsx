import type { ReactNode } from 'react'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'

type Props = { title: string; actions?: ReactNode; children: ReactNode }

export function Page({ title, actions, children }: Props) {
  return (
    <>
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        <SidebarTrigger />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-base font-medium">{title}</h1>
        <div className="ml-auto flex items-center gap-3">{actions}</div>
      </header>
      <main className="flex w-full max-w-4xl flex-col gap-6 p-4 md:p-6">{children}</main>
    </>
  )
}

export function PageSkeleton({ title }: { title: string }) {
  return (
    <Page title={title}>
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-2/3" />
    </Page>
  )
}
