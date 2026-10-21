import { Fragment, useEffect } from 'react'
import { Activity, ArrowLeft, Bell, Bot, Brain, CircleAlert, ListChecks, Mic, Monitor, Moon, Puzzle, Radio, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { NavLink, useLocation } from 'react-router'
import { needsAttention, useStatus } from '@/api/status'
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
  SidebarSeparator,
  useSidebar,
} from '@/components/ui/sidebar'

function HomeAssistantLogo() {
  return <KindIcon name="homeassistant" />
}

// Status first; then who he is and how he thinks, sounds and hears; how you reach him and what he
// connects to; what he may do and when he speaks up on his own.
const nav = [
  [{ to: '/status', label: 'Status', Icon: Activity }],
  [
    { to: '/assistant', label: 'Assistant', Icon: Bot },
    { to: '/brain', label: 'Brain', Icon: Brain },
    { to: '/voice', label: 'Voice', Icon: Mic },
  ],
  [
    { to: '/ways-to-talk', label: 'Ways to talk', Icon: Radio },
    { to: '/home-assistant', label: 'Home Assistant', Icon: HomeAssistantLogo },
    { to: '/extensions', label: 'Extensions', Icon: Puzzle },
  ],
  [
    { to: '/actions', label: 'Actions', Icon: ListChecks },
    { to: '/speaking-up', label: 'Speaking up', Icon: Bell },
  ],
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
  const { setOpenMobile } = useSidebar()
  const status = useStatus()
  const active = (to: string) => pathname.startsWith(to)
  const dirty = useDirtyPages()
  const attention = needsAttention(status.data)
  // On a phone the menu is a sheet over the page: choosing a page closes it.
  useEffect(() => setOpenMobile(false), [pathname, setOpenMobile])
  return (
    <Sidebar>
      <SidebarHeader className="px-2 py-3">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild className="font-semibold">
              <NavLink to="/">
                <ArrowLeft />
                <span>{status.data?.assistant ?? ''}</span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <nav aria-label="Settings">
          {nav.map((group, i) => (
            <Fragment key={group[0].to}>
              {i > 0 ? <SidebarSeparator /> : null}
              <SidebarGroup>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {group.map(({ to, label, Icon }) => {
                      const warn = to === '/status' && attention
                      return (
                        <SidebarMenuItem key={to}>
                          <SidebarMenuButton asChild isActive={active(to)}>
                            <NavLink to={to}>
                              <Icon />
                              <span>{label}</span>
                            </NavLink>
                          </SidebarMenuButton>
                          {warn || dirty.has(to) ? (
                            <SidebarMenuBadge className="gap-1.5">
                              {warn ? <CircleAlert role="img" aria-label="Needs attention" className="size-4 text-warning" /> : null}
                              {dirty.has(to) ? (
                                <span role="img" aria-label="Unsaved changes" className="size-2 rounded-full bg-primary" />
                              ) : null}
                            </SidebarMenuBadge>
                          ) : null}
                        </SidebarMenuItem>
                      )
                    })}
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            </Fragment>
          ))}
        </nav>
      </SidebarContent>
      <SidebarFooter className="flex-row items-center gap-1 px-3">
        <ThemeMenu />
        <span className="ml-auto text-xs text-muted-foreground">{status.data?.version}</span>
      </SidebarFooter>
    </Sidebar>
  )
}
