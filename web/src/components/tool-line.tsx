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
import { useEffect, useState } from 'react'
import { DynamicIcon, iconNames, type IconName } from 'lucide-react/dynamic'
import { isKind, KindIcon, Logo } from '@/components/kind-icon'
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

// A maker's logo is named `simple-icons:<slug>`, loaded the first time it is drawn.
const LOGO = 'simple-icons:'
const isLogo = (name?: string | null): name is string => !!name?.startsWith(LOGO)
type Brand = { title: string; path: string }
const logos = new Map<string, Promise<Brand | null>>()

function loadLogo(slug: string) {
  let logo = logos.get(slug)
  if (!logo) {
    logo = import('@/lib/logos').then(async ({ LOGOS }) => {
      const file = LOGOS[`/node_modules/simple-icons/icons/${slug}.svg`]
      if (!file) return null
      const svg = await file()
      return { title: svg.match(/<title>([^<]*)<\/title>/)?.[1] ?? slug, path: svg.match(/ d="([^"]*)"/)?.[1] ?? '' }
    })
    logos.set(slug, logo)
  }
  return logo
}

/** A logo by its slug; the puzzle piece when Simple Icons has none by that name. */
function ExtensionLogo({ slug }: { slug: string }) {
  const [logo, setLogo] = useState<Brand | null>()
  useEffect(() => {
    let live = true
    void loadLogo(slug).then((l) => live && setLogo(l))
    return () => {
      live = false
    }
  }, [slug])
  if (logo === undefined) return <span className="size-4 shrink-0" />
  return logo ? <Logo path={logo.path} /> : <DynamicIcon name="puzzle" className="size-4 shrink-0" />
}

/** An extension's own icon: a lucide name, a maker's logo (`simple-icons:spotify`), or the puzzle piece. */
export function ExtensionIcon({ name }: { name?: string | null }) {
  if (isLogo(name)) return <ExtensionLogo slug={name.slice(LOGO.length)} />
  return <DynamicIcon name={isIcon(name) ? name : 'puzzle'} className="size-4 shrink-0" />
}

export function ToolIcon({ name, running, icon }: { name: string; running: boolean; icon?: string | null }) {
  if (running) return <LoaderCircle className="size-4 shrink-0 animate-spin" />
  if (isIcon(icon) || isLogo(icon)) return <ExtensionIcon name={icon} />
  // Home Assistant's tools by the domain they come from ("vacuum__HassVacuumStart"); Assist's own by their name.
  const domain = name.includes('__') ? name.split('__')[0] : ''
  if (isKind(domain)) return <KindIcon name={domain} />
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
