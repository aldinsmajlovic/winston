import {
  BookmarkMinus,
  BookmarkPlus,
  Clock,
  Flag,
  House,
  Lightbulb,
  LoaderCircle,
  MessageCircleQuestion,
  MessageSquarePlus,
  Search,
  Send,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const TOOL_ICONS: [(base: string) => boolean, LucideIcon][] = [
  [(b) => b.startsWith('hass'), Lightbulb],
  [(b) => b === 'getlivecontext' || b === 'get_sensor_readings', House],
  [(b) => b === 'getdatetime', Clock],
  [(b) => b === 'web_search', Search],
  [(b) => b === 'ask_claude', MessageCircleQuestion],
  [(b) => b === 'remember', BookmarkPlus],
  [(b) => b === 'forget', BookmarkMinus],
  [(b) => b === 'send_message', Send],
  [(b) => b === 'mark_mistake', Flag],
  [(b) => b === 'start_over', MessageSquarePlus],
]

export function ToolIcon({ name, running }: { name: string; running: boolean }) {
  if (running) return <LoaderCircle className="size-4 shrink-0 animate-spin" />
  const base = name.split('__').pop()?.toLowerCase() ?? ''
  const Icon = TOOL_ICONS.find(([match]) => match(base))?.[1] ?? Wrench
  return <Icon className="size-4 shrink-0" />
}

/** A tool call as one muted line with its icon: "Searched the web for ...". A record of what he did, not a control. */
export function ToolLine({ name, label, status }: { name: string; label: string; status: string }) {
  return (
    <div className={cn('flex items-center gap-2 text-sm', status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
      <ToolIcon name={name} running={status === 'running'} />
      <span>{label}</span>
    </div>
  )
}
