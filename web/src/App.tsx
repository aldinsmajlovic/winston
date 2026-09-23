import { Navigate, Outlet, Route, Routes } from 'react-router'
import { AppSidebar } from '@/components/app-sidebar'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import AssistantPage from '@/pages/assistant'
import BrainPage from '@/pages/brain'
import ChannelsPage from '@/pages/channels'
import EnginePage from '@/pages/engine'
import EnginesPage from '@/pages/engines'
import HomeAssistantPage from '@/pages/home-assistant'
import ModelsPage from '@/pages/models'
import SpeechPage from '@/pages/speech'
import StatusPage from '@/pages/status'
import SystemPage from '@/pages/system'
import TalkPage from '@/pages/talk'
import ToolsPage from '@/pages/tools'

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
  return (
    <Routes>
      <Route path="/" element={<TalkPage />} />
      <Route element={<SettingsLayout />}>
        <Route path="/status" element={<StatusPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/brain" element={<BrainPage />} />
        <Route path="/speech" element={<SpeechPage />} />
        <Route path="/channels" element={<ChannelsPage />} />
        <Route path="/home-assistant" element={<HomeAssistantPage />} />
        <Route path="/tools" element={<ToolsPage />} />
        <Route path="/engines" element={<EnginesPage />} />
        <Route path="/engines/:name" element={<EnginePage />} />
        <Route path="/system" element={<SystemPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
