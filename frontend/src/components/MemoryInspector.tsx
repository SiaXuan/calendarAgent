import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  deleteMemory,
  listMemories,
  patchMemory,
  type Memory,
  type MemoryNamespace,
} from '../api/memory'

/**
 * Phase C.2 — Memory Inspector.
 * Read/edit/delete agent memories. Lives inside the Settings page as a section.
 * Per the plan: this MUST exist before any auto-write path lands (C.3+).
 */

const NAMESPACE_LABELS: Record<MemoryNamespace, string> = {
  schedule_prefs: 'memoryNamespaceSchedulePrefs',
  task_lexicon: 'memoryNamespaceTaskLexicon',
  physiological: 'memoryNamespacePhysiological',
  interactions: 'memoryNamespaceInteractions',
}

const NAMESPACES: MemoryNamespace[] = [
  'schedule_prefs',
  'task_lexicon',
  'physiological',
  'interactions',
]


function formatRelative(iso: string): string {
  const t = new Date(iso).getTime()
  const diffSec = Math.round((Date.now() - t) / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.round(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffH = Math.round(diffMin / 60)
  if (diffH < 24) return `${diffH}h ago`
  const diffD = Math.round(diffH / 24)
  return `${diffD}d ago`
}


function MemoryRow({ memory, onEdited, onDeleted }: {
  memory: Memory
  onEdited: () => void
  onDeleted: () => void
}) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [draftContent, setDraftContent] = useState(memory.content)
  const [draftConfidence, setDraftConfidence] = useState(memory.confidence)

  const save = useMutation({
    mutationFn: () => patchMemory(memory.id, {
      content: draftContent,
      confidence: draftConfidence,
    }),
    onSuccess: () => { setEditing(false); onEdited() },
  })

  const verify = useMutation({
    mutationFn: () => patchMemory(memory.id, { user_verified: true }),
    onSuccess: onEdited,
  })

  const remove = useMutation({
    mutationFn: () => deleteMemory(memory.id),
    onSuccess: onDeleted,
  })

  const confidencePct = Math.round(memory.confidence * 100)
  const verifiedStyle = memory.user_verified
    ? { background: '#E8F1E8', color: '#3D7C47' }
    : { background: '#F0F4F8', color: '#6B7785' }

  return (
    <div className="px-4 py-3 border-b border-ice2 last:border-0">
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea
            value={draftContent}
            onChange={e => setDraftContent(e.target.value)}
            className="text-[13px] text-[#1a2730] border border-gray-border rounded-md p-2 min-h-[60px] resize-y"
          />
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-gray-text">{t('memoryConfidence')}</span>
            <input
              type="range" min="0" max="1" step="0.05"
              value={draftConfidence}
              onChange={e => setDraftConfidence(Number(e.target.value))}
              className="flex-1"
            />
            <span className="text-[11px] text-gray-text w-8 text-right">
              {Math.round(draftConfidence * 100)}%
            </span>
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => { setEditing(false); setDraftContent(memory.content); setDraftConfidence(memory.confidence) }}
              className="text-[12px] text-gray-text px-3 py-1 rounded hover:bg-ice"
            >{t('memoryCancel')}</button>
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="text-[12px] text-white bg-blue-deep px-3 py-1 rounded disabled:opacity-50"
            >{t('memorySave')}</button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <div className="text-[13px] text-[#1a2730] leading-relaxed">{memory.content}</div>
          <div className="flex items-center gap-2 text-[11px] text-gray-text">
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-medium"
              style={verifiedStyle}
            >
              {memory.user_verified ? t('memoryVerified') : t('memoryUnverified')}
            </span>
            <span>{confidencePct}% {t('memoryConfidence')}</span>
            <span>·</span>
            <span>{t('memoryReinforced', { when: formatRelative(memory.last_reinforced_at) })}</span>
          </div>
          <div className="flex gap-1.5 mt-1">
            <button
              onClick={() => setEditing(true)}
              className="text-[11px] text-gray-text hover:text-blue-deep px-2 py-0.5"
            >{t('memoryEdit')}</button>
            {!memory.user_verified && (
              <button
                onClick={() => verify.mutate()}
                disabled={verify.isPending}
                className="text-[11px] text-gray-text hover:text-blue-deep px-2 py-0.5"
              >{t('memoryConfirm')}</button>
            )}
            <button
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
              className="text-[11px] text-gray-text hover:text-red-600 px-2 py-0.5 ml-auto"
            >{t('memoryDelete')}</button>
          </div>
        </div>
      )}
    </div>
  )
}


export default function MemoryInspector() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [selectedNs, setSelectedNs] = useState<MemoryNamespace | 'all'>('all')

  const { data: memories = [], isLoading } = useQuery({
    queryKey: ['memory', selectedNs],
    queryFn: () => listMemories(selectedNs === 'all' ? {} : { namespace: selectedNs }),
  })

  const refetch = () => qc.invalidateQueries({ queryKey: ['memory'] })

  return (
    <div className="bg-white border border-gray-border rounded-2xl overflow-hidden">
      <div className="px-4 py-3 border-b border-ice2 flex items-center justify-between">
        <span className="text-[12px] text-gray-text uppercase tracking-wide">{t('memory')}</span>
        <span className="text-[11px] text-gray-text">{memories.length}</span>
      </div>

      {/* namespace filter chips */}
      <div className="px-4 py-2.5 flex flex-wrap gap-1.5 border-b border-ice2">
        <NsChip label={t('memoryAll')} active={selectedNs === 'all'} onClick={() => setSelectedNs('all')} />
        {NAMESPACES.map(ns => (
          <NsChip
            key={ns}
            label={t(NAMESPACE_LABELS[ns])}
            active={selectedNs === ns}
            onClick={() => setSelectedNs(ns)}
          />
        ))}
      </div>

      {/* list */}
      {isLoading ? (
        <div className="px-4 py-6 text-[12px] text-gray-text text-center">{t('loading')}</div>
      ) : memories.length === 0 ? (
        <div className="px-4 py-6 text-[12px] text-gray-text text-center leading-relaxed">
          {t('memoryEmpty')}
        </div>
      ) : (
        memories.map(m => (
          <MemoryRow key={m.id} memory={m} onEdited={refetch} onDeleted={refetch} />
        ))
      )}
    </div>
  )
}


function NsChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-[11px] px-2.5 py-1 rounded-full"
      style={{
        background: active ? '#4E8BB5' : '#F0F4F8',
        color: active ? 'white' : '#6B7785',
      }}
    >
      {label}
    </button>
  )
}
