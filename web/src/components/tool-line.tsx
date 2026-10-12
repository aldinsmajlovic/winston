import {
  BookmarkMinus,
  BookmarkPlus,
  Clock,
  Flag,
  House,
  Lightbulb,
  LoaderCircle,
  MessageSquarePlus,
  Send,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { DynamicIcon, iconNames, type IconName } from 'lucide-react/dynamic'
import { cn } from '@/lib/utils'

const TOOL_ICONS: [(base: string) => boolean, LucideIcon][] = [
  [(b) => b.startsWith('hass'), Lightbulb],
  [(b) => b === 'getlivecontext', House],
  [(b) => b === 'getdatetime', Clock],
  [(b) => b === 'remember', BookmarkPlus],
  [(b) => b === 'forget', BookmarkMinus],
  [(b) => b === 'send_message', Send],
  [(b) => b === 'mark_mistake', Flag],
  [(b) => b === 'start_over', MessageSquarePlus],
]

const isIcon = (name?: string | null): name is IconName => !!name && (iconNames as readonly string[]).includes(name)

/** An extension's own icon (a lucide name), or the puzzle piece. */
export function ExtensionIcon({ name }: { name?: string | null }) {
  return <DynamicIcon name={isIcon(name) ? name : 'puzzle'} className="size-4 shrink-0" />
}

export function ToolIcon({ name, running, icon }: { name: string; running: boolean; icon?: string | null }) {
  if (running) return <LoaderCircle className="size-4 shrink-0 animate-spin" />
  if (isIcon(icon)) return <ExtensionIcon name={icon} />
  const base = name.split('__').pop()?.toLowerCase() ?? ''
  const Icon = TOOL_ICONS.find(([match]) => match(base))?.[1] ?? Wrench
  return <Icon className="size-4 shrink-0" />
}

/** A tool call as one muted line with its icon: "Turned on Kitchen lights". A record of what he did, not a control. */
export function ToolLine({ name, label, status, icon }: { name: string; label: string; status: string; icon?: string | null }) {
  return (
    <div className={cn('flex items-center gap-2 text-sm', status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
      <ToolIcon name={name} running={status === 'running'} icon={icon} />
      <span>{label}</span>
    </div>
  )
}
