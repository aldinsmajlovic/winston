import { Activity, ArrowLeft, BookOpen, Bot, Brain, Cpu, Mic, Monitor, Moon, Puzzle, Radio, Settings, Sun, Wrench } from 'lucide-react'
import { useTheme } from 'next-themes'
import { NavLink, useLocation } from 'react-router'
import { $api } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { KindIcon } from '@/components/kind-icon'
import { useDirtyPages } from '@/hooks/use-settings'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar'

function HomeAssistantLogo() {
  return <KindIcon name="homeassistant" />
}

const nav = [
  { to: '/status', label: 'Status', Icon: Activity },
  { to: '/assistant', label: 'Assistant', Icon: Bot },
  { to: '/brain', label: 'Brain', Icon: Brain },
  { to: '/voice', label: 'Voice', Icon: Mic },
  { to: '/channels', label: 'Channels', Icon: Radio },
  { to: '/home-assistant', label: 'Home Assistant', Icon: HomeAssistantLogo },
  { to: '/tools', label: 'Tools', Icon: Wrench },
  { to: '/extensions', label: 'Extensions', Icon: Puzzle },
  { to: '/engines', label: 'Engines', Icon: Cpu },
  { to: '/system', label: 'System', Icon: Settings },
]

function ThemeMenu() {
  const { theme = 'system', setTheme, resolvedTheme } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <IconButton label="Theme" size="icon">
          {resolvedTheme === 'dark' ? <Moon /> : <Sun />}
        </IconButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
          <DropdownMenuRadioItem value="light">
            <Sun />
            Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon />
            Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <Monitor />
            System
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function AppSidebar() {
  const { pathname } = useLocation()
  const status = $api.useQuery('get', '/api/status')
  const active = (to: string) => pathname.startsWith(to)
  const dirty = useDirtyPages()
  return (
    <Sidebar>
      <SidebarHeader className="px-2 py-3">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild className="font-semibold">
              <NavLink to="/">
                <ArrowLeft />
                <span>{status.data?.assistant ?? 'Winston'}</span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {nav.map(({ to, label, Icon }) => (
                <SidebarMenuItem key={to}>
                  <SidebarMenuButton asChild isActive={active(to)}>
                    <NavLink to={to}>
                      <Icon />
                      <span>{label}</span>
                    </NavLink>
                  </SidebarMenuButton>
                  {dirty.has(to) ? (
                    <SidebarMenuBadge>
                      <span role="img" aria-label="Unsaved changes" className="size-2 rounded-full bg-primary" />
                    </SidebarMenuBadge>
                  ) : null}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="flex-row items-center gap-1 px-3">
        <ThemeMenu />
        <IconButton label="API" size="icon" asChild>
          <a href="/docs" target="_blank" rel="noreferrer">
            <BookOpen />
          </a>
        </IconButton>
        <span className="ml-auto text-xs text-muted-foreground">{status.data?.version}</span>
      </SidebarFooter>
    </Sidebar>
  )
}
