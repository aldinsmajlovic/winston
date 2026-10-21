import { Component, type ReactNode } from 'react'
import { generatePath, Navigate, Outlet, Route, Routes, useLocation, useParams } from 'react-router'
import { AppSidebar } from '@/components/app-sidebar'
import { Button } from '@/components/ui/button'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import ActionsPage from '@/pages/actions'
import AssistantPage from '@/pages/assistant'
import BrainPage from '@/pages/brain'
import DashboardPage from '@/pages/dashboard'
import EnginePage from '@/pages/engine'
import ExtensionsPage, { ExtensionPage } from '@/pages/extensions'
import HomeAssistantPage from '@/pages/home-assistant'
import MemoryPage from '@/pages/memory'
import PersonalityPage from '@/pages/personality'
import SpeakingUpPage from '@/pages/speaking-up'
import StatusPage from '@/pages/status'
import TalkPage from '@/pages/talk'
import TurnsPage from '@/pages/turns'
import VoicePage from '@/pages/voice'
import WaysToTalkPage from '@/pages/ways-to-talk'

/** Settings: the sidebar and its pages, behind the Talk page. */
function SettingsLayout() {
  return (
    <SidebarProvider>
      <AppSidebar />
      {/* No wider than the sidebar leaves room for, so a long header truncates instead of scrolling sideways. */}
      <SidebarInset className="min-w-0">
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  )
}

/** A page that moved: its new address, keeping the old one's :params and query (a wall display's ?room=). */
function Moved({ to }: { to: string }) {
  const { search } = useLocation()
  const params = useParams()
  return <Navigate to={generatePath(to, params) + search} replace />
}

/** A page that failed to render, in place of a blank app. Going to another page clears it; the draft
 * lives above App, so unsaved changes survive. */
class PageError extends Component<{ path: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidUpdate(previous: { path: string }) {
    if (this.state.failed && previous.path !== this.props.path) this.setState({ failed: false })
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="flex min-h-svh flex-col items-center justify-center gap-3 p-4 text-center text-muted-foreground">
        <p>This page hit a problem. Your unsaved changes are kept.</p>
        <Button variant="outline" onClick={() => this.setState({ failed: false })}>
          Try again
        </Button>
      </div>
    )
  }
}

export default function App() {
  const { pathname } = useLocation()
  return (
    <PageError path={pathname}>
      <Routes>
        <Route path="/" element={<TalkPage />} />
        <Route path="/history" element={<TurnsPage />} />
        <Route path="/turns" element={<Moved to="/history" />} />
        <Route path="/diary" element={<Moved to="/history" />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/wall" element={<DashboardPage />} />
        <Route path="/dashboard" element={<Moved to="/wall" />} />
        <Route element={<SettingsLayout />}>
          <Route path="/status" element={<StatusPage />} />
          <Route path="/status/engines/:name" element={<EnginePage />} />
          <Route path="/system" element={<Moved to="/status" />} />
          <Route path="/system/engines/:name" element={<Moved to="/status/engines/:name" />} />
          <Route path="/engines" element={<Moved to="/status" />} />
          <Route path="/models" element={<Navigate to="/brain" replace />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/assistant/personality" element={<PersonalityPage />} />
          <Route path="/brain" element={<BrainPage />} />
          <Route path="/voice" element={<VoicePage />} />
          <Route path="/speech" element={<Navigate to="/voice" replace />} />
          <Route path="/ways-to-talk" element={<WaysToTalkPage />} />
          <Route path="/channels" element={<Moved to="/ways-to-talk" />} />
          <Route path="/home-assistant" element={<HomeAssistantPage />} />
          <Route path="/extensions" element={<ExtensionsPage />} />
          <Route path="/extensions/speaking-up" element={<Moved to="/speaking-up" />} />
          <Route path="/extensions/:id" element={<ExtensionPage />} />
          <Route path="/actions" element={<ActionsPage />} />
          <Route path="/speaking-up" element={<SpeakingUpPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </PageError>
  )
}
