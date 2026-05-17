import { useState, useEffect } from 'react'
import axios from 'axios'

export default function SettingsPanel({ onClose }) {
  const [host, setHost] = useState('http://localhost:11434')
  const [model, setModel] = useState('')
  const [models, setModels] = useState([])
  const [checkState, setCheckState] = useState(null) // null | 'checking' | 'ok' | 'error'
  const [checkMsg, setCheckMsg] = useState('')
  const [pullModel, setPullModel] = useState('')
  const [pullState, setPullState] = useState(null) // null | 'pulling' | 'ok' | 'error'
  const [pullMsg, setPullMsg] = useState('')

  // Load saved settings on mount
  useEffect(() => {
    axios.get('/api/workspace').then(r => {
      setHost(r.data.host || 'http://localhost:11434')
      setModel(r.data.model || '')
    }).catch(() => {})
  }, [])

  const handleCheck = async () => {
    setCheckState('checking')
    setCheckMsg('')
    try {
      const r = await axios.get('/api/ollama/status', { params: { host } })
      if (r.data.alive) {
        const ms = r.data.models || []
        setModels(ms)
        if (!model && ms.length) setModel(ms[0])
        const v = r.data.version ? ` (v${r.data.version})` : ''
        setCheckMsg(`Connected${v} — ${ms.length} model(s) available`)
        setCheckState('ok')
        saveSettings(host, model || ms[0] || '')
      } else {
        setCheckMsg(`Ollama unreachable at ${host}`)
        setCheckState('error')
      }
    } catch {
      setCheckMsg('Request failed')
      setCheckState('error')
    }
  }

  const saveSettings = (h, m) => {
    axios.put('/api/workspace/settings', { host: h, model: m }).catch(() => {})
  }

  const handleModelChange = (m) => {
    setModel(m)
    saveSettings(host, m)
  }

  const handlePull = async () => {
    if (!pullModel.trim()) return
    setPullState('pulling')
    setPullMsg('Starting pull…')
    try {
      const response = await fetch('/api/ollama/pull', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: pullModel.trim(), host }),
      })
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const lines = decoder.decode(value).split('\n').filter(Boolean)
        for (const line of lines) {
          try {
            const event = JSON.parse(line)
            if (event._qls_status === 'ok') {
              setPullState('ok')
              setPullMsg(`✓ Pulled ${pullModel} successfully`)
              // Refresh model list
              handleCheck()
              return
            } else if (event._qls_status === 'failed') {
              setPullState('error')
              setPullMsg(`Pull failed: ${event.error}`)
              return
            } else if (event.status) {
              const pct = event.total ? Math.round((event.completed / event.total) * 100) : null
              setPullMsg(pct != null ? `${event.status} — ${pct}%` : event.status)
            }
          } catch { /* ignore malformed lines */ }
        }
      }
    } catch (e) {
      setPullState('error')
      setPullMsg(`Error: ${e.message}`)
    }
  }

  return (
    <div className="w-80 bg-white border-l border-gray-200 flex flex-col h-full overflow-y-auto flex-shrink-0">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <h2 className="font-semibold text-gray-900 text-sm">Settings</h2>
        <button
          onClick={onClose}
          className="w-7 h-7 rounded-md flex items-center justify-center text-gray-400
                     hover:bg-gray-100 hover:text-gray-600 transition-colors text-sm"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 px-4 py-4 space-y-6">

        {/* Ollama connection */}
        <section>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
            Ollama
          </h3>
          <div className="space-y-2">
            <label className="block">
              <span className="text-xs font-medium text-gray-700">Host</span>
              <input
                type="text"
                value={host}
                onChange={e => setHost(e.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2.5 py-1.5
                           text-sm text-gray-900 placeholder-gray-400
                           focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
                placeholder="http://localhost:11434"
              />
            </label>

            <button
              onClick={handleCheck}
              disabled={checkState === 'checking'}
              className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium
                         text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
            >
              {checkState === 'checking' ? 'Checking…' : 'Check connection'}
            </button>

            {checkMsg && (
              <p className={`text-xs ${checkState === 'ok' ? 'text-emerald-600' : 'text-red-500'}`}>
                {checkMsg}
              </p>
            )}

            {models.length > 0 && (
              <label className="block">
                <span className="text-xs font-medium text-gray-700">Model</span>
                <select
                  value={model}
                  onChange={e => handleModelChange(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-2.5 py-1.5
                             text-sm text-gray-900 focus:border-indigo-500 focus:ring-1
                             focus:ring-indigo-500 outline-none bg-white"
                >
                  {models.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
            )}
          </div>
        </section>

        {/* Pull model */}
        <section>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
            Pull model
          </h3>
          <div className="space-y-2">
            <input
              type="text"
              value={pullModel}
              onChange={e => setPullModel(e.target.value)}
              placeholder="e.g. qwen3:4b, gemma3:4b"
              className="block w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm
                         text-gray-900 placeholder-gray-400 focus:border-indigo-500
                         focus:ring-1 focus:ring-indigo-500 outline-none"
            />
            <button
              onClick={handlePull}
              disabled={!pullModel.trim() || pullState === 'pulling'}
              className="w-full rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium
                         text-white hover:bg-indigo-600 disabled:opacity-50 transition-colors"
            >
              {pullState === 'pulling' ? 'Pulling…' : 'Pull'}
            </button>
            {pullMsg && (
              <p className={`text-xs ${pullState === 'ok' ? 'text-emerald-600' : pullState === 'error' ? 'text-red-500' : 'text-gray-500'}`}>
                {pullMsg}
              </p>
            )}
          </div>
        </section>

      </div>
    </div>
  )
}
