import { useState } from 'react'
import { UserPreferencesProvider } from './context/UserPreferencesContext'
import BottomNav from './components/BottomNav'
import TodayPage from './pages/TodayPage'
import TasksPage from './pages/TasksPage'
import ChatPage from './pages/ChatPage'
import SettingsPage from './pages/SettingsPage'

type Tab = 'today' | 'tasks' | 'chat' | 'settings'

export default function App() {
  const [tab, setTab] = useState<Tab>('today')

  const page = {
    today: <TodayPage />,
    tasks: <TasksPage />,
    chat: <ChatPage />,
    settings: <SettingsPage />,
  }[tab]

  return (
    <UserPreferencesProvider>
      <div className="h-full flex flex-col max-w-sm mx-auto relative overflow-hidden bg-[#F7FAFE]"
           style={{ boxShadow: '0 0 0 0.5px #D8DDE3' }}>
        <div className="flex-1 flex flex-col overflow-hidden relative">
          {page}
        </div>
        <BottomNav active={tab} onChange={setTab} />
        <div className="h-1.5 bg-[#F7FAFE]">
          <div className="w-[134px] h-1 bg-[#1a2730] rounded-full mx-auto opacity-[0.12]" />
        </div>
      </div>
    </UserPreferencesProvider>
  )
}
