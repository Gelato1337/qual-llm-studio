import { useState, useEffect } from 'react'
import axios from 'axios'

export default function Header({
  onSettingsToggle, settingsOpen,
  onExport, onImport, onRunAll, onNew,
}) {
  const [status, setStatus] = useState({ alive: false, model: '' })

  useEffect(() => {
    const check = () =>
      axios.get('/api/workspace')
        .then(r => {
          const { host, model } = r.data
          return axios.get('/api/ollama/status', { params: { host } })
            .then(s => setStatus({ alive: s.data.alive, model }))
        })
        .catch(() => setStatus({ alive: false, model: '' }))

    check()
    const id = setInterval(check, 15000)
    return () => clearInterval(id)
  }, [])

  return (
    <header className="h-12 flex items-center justify-between px-4 bg-white border-b border-gray-200 flex-shrink-0 z-10">

      {/* Left: wordmark */}
      <div className="flex items-center gap-2.5">
        <div className="w-6 h-6 rounded bg-indigo-500 flex items-center justify-center">
          <span className="text-white text-xs font-bold">Q</span>
        </div>
        <span className="font-semibold text-gray-900 text-sm tracking-tight">
          Qual LLM Studio
        </span>
      </div>

      {/* Centre: pipeline actions */}
      <div className="flex items-center gap-1.5">
        {/* Run All */}
        <button
          onClick={onRunAll}
          className="flex items-center gap-1.5 rounded-md bg-emerald-500 hover:bg-emerald-600
                     px-3 py-1.5 text-xs font-semibold text-white transition-colors"
          title="Trigger all ready Run blocks"
        >
          <span>▶▶</span>
          <span>Run All</span>
        </button>

        <div className="w-px h-5 bg-gray-200 mx-0.5" />

        {/* Export */}
        <button
          onClick={onExport}
          className="w-8 h-8 rounded-md flex items-center justify-center text-gray-500
                     hover:bg-gray-100 hover:text-gray-700 transition-colors text-sm"
          title="Export pipeline as JSON"
        >
          ↓
        </button>

        {/* Import */}
        <button
          onClick={onImport}
          className="w-8 h-8 rounded-md flex items-center justify-center text-gray-500
                     hover:bg-gray-100 hover:text-gray-700 transition-colors text-sm"
          title="Import pipeline from JSON"
        >
          ↑
        </button>

        {/* New canvas */}
        <button
          onClick={onNew}
          className="w-8 h-8 rounded-md flex items-center justify-center text-gray-500
                     hover:bg-gray-100 hover:text-gray-700 transition-colors text-sm"
          title="New canvas"
        >
          ✕
        </button>
      </div>

      {/* Right: Ollama status + settings cog */}
      <div className="flex items-center gap-3">
        <StatusBadge alive={status.alive} model={status.model} />
        <button
          onClick={onSettingsToggle}
          className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors text-sm
            ${settingsOpen
              ? 'bg-indigo-50 text-indigo-600'
              : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'
            }`}
          title="Settings"
        >
          ⚙
        </button>
      </div>
    </header>
  )
}

function StatusBadge({ alive, model }) {
  if (alive && model) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-gray-500">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
        <span className="font-mono text-gray-600">{model}</span>
      </div>
    )
  }
  if (alive) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-amber-600">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        <span>Connected — no model</span>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-1.5 text-xs text-gray-400">
      <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
      <span>Ollama offline</span>
    </div>
  )
}
