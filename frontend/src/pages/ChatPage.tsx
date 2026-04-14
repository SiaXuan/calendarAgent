import { useTranslation } from 'react-i18next'

export default function ChatPage() {
  const { t } = useTranslation()
  return (
    <div className="flex-1 flex items-center justify-center bg-ice">
      <span className="text-[15px] text-gray-text">{t('chat')}</span>
    </div>
  )
}
