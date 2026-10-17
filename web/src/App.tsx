import { useEffect } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router'
import { $api } from '@/api/client'
import { AppSidebar } from '@/components/app-sidebar'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import AssistantPage from '@/pages/assistant'
import BrainPage from '@/pages/brain'
import ChannelsPage from '@/pages/channels'
import DashboardPage from '@/pages/dashboard'
import EnginePage from '@/pages/engine'
import ExtensionsPage, { ExtensionPage, SpeakingUpPage } from '@/pages/extensions'
import HomeAssistantPage from '@/pages/home-assistant'
import MemoryPage from '@/pages/memory'
import TurnsPage from '@/pages/turns'
import SystemPage from '@/pages/system'
import TalkPage from '@/pages/talk'
import VoicePage from '@/pages/voice'

/** Settings: the sidebar and its pages, behind the Talk page. */
function SettingsLayout() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  )
}

export default function App() {
  const name = $api.useQuery('get', '/api/status').data?.assistant
  useEffect(() => {
    if (name) document.title = name
  }, [name])
  return (
    <Routes>
      <Route path="/" element={<TalkPage />} />
      <Route path="/turns" element={<TurnsPage />} />
      <Route path="/memory" element={<MemoryPage />} />
      <Route path="/diary" element={<Navigate to="/turns" replace />} />
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route element={<SettingsLayout />}>
        <Route path="/status" element={<Navigate to="/system" replace />} />
        <Route path="/models" element={<Navigate to="/brain" replace />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/brain" element={<BrainPage />} />
        <Route path="/voice" element={<VoicePage />} />
        <Route path="/speech" element={<Navigate to="/voice" replace />} />
        <Route path="/channels" element={<ChannelsPage />} />
        <Route path="/home-assistant" element={<HomeAssistantPage />} />
        <Route path="/extensions" element={<ExtensionsPage />} />
        <Route path="/extensions/speaking-up" element={<SpeakingUpPage />} />
        <Route path="/extensions/:id" element={<ExtensionPage />} />
        <Route path="/engines" element={<Navigate to="/system" replace />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="/system/engines/:name" element={<EnginePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
