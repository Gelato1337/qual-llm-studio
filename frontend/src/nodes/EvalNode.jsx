/**
 * EvalNode — "Full Eval"
 *
 * Reads the upstream RunNode's jobId (graph traversal). When the job is done,
 * auto-fills pipeline + version from the run_folder. Also supports manual run
 * selection via a dropdown populated from GET /api/execute/runs/list.
 *
 * Flow:
 *   idle     → pick run + Start Eval Session
 *   active   → sample rows, label each row (human), run LLM judge, view stats
 *
 * Calls:
 *   GET  /api/execute/runs/list              → populate run picker
 *   GET  /api/execute/{job_id}               → resolve run_folder from upstream job
 *   POST /api/eval/sessions                  → create / resume session
 *   GET  /api/eval/sessions/{sid}/sample     → pick rows for this cycle
 *   POST /api/eval/sessions/{sid}/judge      → record human label
 *   POST /api/eval/sessions/{sid}/llm        → run LLM judge on current rows
 *   GET  /api/eval/sessions/{sid}/stats      → agreement metrics
 */

import { useCallback, useEffect, useState } from 'react'
import { Handle, Position, useEdges, useNodes, useReactFlow } from 'reactflow'
import {
  getJob, listRuns,
  createEvalSession, sampleEval, judgeHuman, judgeLLM, evalStats,
} from '../lib/api.js'

const LABEL_COLORS = {
  good:       'bg-emerald-500 hover:bg-emerald-600 text-white border-emerald-500',
  bad:        'bg-red-500    hover:bg-red-600    text-white border-red-500',
  borderline: 'bg-amber-400  hover:bg-amber-500  text-white border-amber-400',
}
const LABEL_DEFAULT = 'bg-white hover:bg-gray-50 text-gray-600 border-gray-300'

export default function EvalNode({ id, data }) {
  const { setNodes } = useReactFlow()
  const allNodes = useNodes()
  const allEdges = useEdges()

  // Graph traversal — read upstream RunNode
  const inEdge     = allEdges.find(e => e.target === id)
  const sourceNode = allNodes.find(n => n.id === inEdge?.source)
  const upstreamJobId     = sourceNode?.data?.jobId
  const upstreamJobStatus = sourceNode?.data?.jobStatus

  // ── State ──────────────────────────────────────────────────────────────────
  const [phase,        setPhase]        = useState(data.phase       || 'idle')
  const [runs,         setRuns]         = useState([])
  const [pipeline,     setPipeline]     = useState(data.pipeline    || '')
  const [version,      setVersion]      = useState(data.version     || '')
  const [sessionId,    setSessionId]    = useState(data.sessionId   || '')
  const [nRows,        setNRows]        = useState(data.nRows       || 0)
  const [labelOptions, setLabelOptions] = useState(data.labelOptions || ['good', 'bad', 'borderline'])
  const [sampleRows,   setSampleRows]   = useState([])
  const [sampleCols,   setSampleCols]   = useState([])
  const [labels,       setLabels]       = useState({})   // row_id → label
  const [llmLabels,    setLlmLabels]    = useState({})   // row_id → llm label
  const [stats,        setStats]        = useState(null)
  const [llmModel,     setLlmModel]     = useState(data.model || '')
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState('')
  const [statusMsg,    setStatusMsg]    = useState('')

  const updateData = useCallback((updates) => {
    setNodes(nds => nds.map(n =>
      n.id === id ? { ...n, data: { ...n.data, ...updates } } : n
    ))
  }, [id, setNodes])

  // Load run list for dropdown
  useEffect(() => {
    listRuns()
      .then(r => setRuns(r.data.runs ?? []))
      .catch(() => {})
  }, [])

  // When upstream job completes, auto-resolve pipeline + version
  useEffect(() => {
    if (upstreamJobStatus !== 'done' || !upstreamJobId || pipeline) return
    getJob(upstreamJobId)
      .then(r => {
        const folder = r.data.run_folder  // e.g. ".../runs/my_pipeline/v1_model"
        if (!folder) return
        const parts = folder.replace(/\\/g, '/').split('/')
        const ver  = parts[parts.length - 1]
        const pipe = parts[parts.length - 2]
        if (pipe && ver) {
          setPipeline(pipe)
          setVersion(ver)
          setLlmModel(r.data.model || '')
        }
      })
      .catch(() => {})
  }, [upstreamJobStatus, upstreamJobId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handlers ───────────────────────────────────────────────────────────────

  const handleStart = async () => {
    if (!pipeline || !version) return
    setLoading(true)
    setError('')
    try {
      const { data: res } = await createEvalSession(pipeline, version)
      const sid = res.session_id
      setSessionId(sid)
      setNRows(res.n_rows)
      setLabelOptions(res.label_options ?? ['good', 'bad', 'borderline'])
      setPhase('active')
      setLabels({})
      setLlmLabels({})
      setSampleRows([])
      updateData({ phase: 'active', sessionId: sid, pipeline, version, nRows: res.n_rows })
      // Auto-sample
      await _fetchSample(sid)
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }

  const _fetchSample = async (sid) => {
    const { data: res } = await sampleEval(sid, 5)
    const rows = res.rows ?? []
    setSampleRows(rows)
    // Visible columns: non-internal, up to 3
    const cols = rows.length > 0
      ? Object.keys(rows[0]).filter(c => !c.startsWith('_') && c !== 'doc_id').slice(0, 3)
      : []
    setSampleCols(cols)
    setLabels({})
    setStatusMsg(res.strategy ? `Strategy: ${res.strategy} · Cycle ${res.cycle}` : '')
  }

  const handleNextSample = async () => {
    if (!sessionId) return
    setLoading(true)
    setError('')
    try {
      await _fetchSample(sessionId)
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleLabel = async (rowId, label) => {
    setLabels(prev => ({ ...prev, [rowId]: label }))
    try {
      await judgeHuman(sessionId, rowId, label)
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message)
    }
  }

  const handleLLMJudge = async () => {
    if (!sampleRows.length) return
    setLoading(true)
    setError('')
    try {
      const rowIds = sampleRows.map(r => String(r.doc_id ?? r._row_id ?? ''))
      const { data: res } = await judgeLLM(sessionId, {
        row_ids: rowIds,
        model: llmModel,
      })
      const verdicts = res.verdicts ?? {}
      setLlmLabels(prev => ({ ...prev, ...verdicts }))
      // Refresh stats after LLM judge
      await _fetchStats()
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }

  const _fetchStats = async () => {
    try {
      const { data: res } = await evalStats(sessionId)
      setStats(res)
      updateData({ kappa: res.cohens_kappa, exactMatch: res.exact_match_rate })
    } catch {
      // stats not available yet — ignore
    }
  }

  const handleStats = async () => {
    if (!sessionId) return
    setLoading(true)
    try {
      await _fetchStats()
    } finally {
      setLoading(false)
    }
  }

  // ── Run picker helpers ─────────────────────────────────────────────────────

  const handleRunSelect = (e) => {
    const val = e.target.value   // "pipeline::version"
    if (!val) { setPipeline(''); setVersion(''); return }
    const [p, v] = val.split('::')
    setPipeline(p)
    setVersion(v)
  }

  const selectedRunValue = pipeline && version ? `${pipeline}::${version}` : ''
  const canStart = pipeline && version && !loading

  // ── Render ─────────────────────────────────────────────────────────────────

  const hasRows  = sampleRows.length > 0
  const nodeWidth = phase === 'active' && hasRows ? 380 : 260

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden"
         style={{ width: nodeWidth }}>

      {/* ── Header ── */}
      <div className="bg-rose-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base leading-none">📊</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Full Eval</p>
          <p className="text-rose-200 text-xs mt-0.5">Journal</p>
        </div>
        {nRows > 0 && (
          <span className="bg-rose-400 text-white text-xs px-1.5 py-0.5 rounded-full font-mono leading-none">
            {nRows}
          </span>
        )}
      </div>

      {/* ── Body ── */}
      <div className="px-3 py-3 space-y-2.5 nodrag">

        {/* ─ IDLE phase ─ */}
        {phase === 'idle' && (
          <>
            {/* Upstream auto-fill hint */}
            {upstreamJobStatus === 'done' && pipeline && version && (
              <div className="bg-emerald-50 border border-emerald-100 rounded-md px-2 py-1.5 text-xs text-emerald-700">
                Run detected: <span className="font-mono">{pipeline}/{version}</span>
              </div>
            )}

            {/* Manual run picker */}
            {(!pipeline || !version) && (
              <div>
                <p className="text-xs font-medium text-gray-700 mb-1">Select run</p>
                <select
                  value={selectedRunValue}
                  onChange={handleRunSelect}
                  className="w-full text-xs rounded-md border border-gray-300 px-2 py-1.5
                             bg-white text-gray-800 focus:border-rose-400 outline-none nowheel"
                >
                  <option value="">— choose a run —</option>
                  {runs.map(r => (
                    <option key={`${r.pipeline}::${r.name}`} value={`${r.pipeline}::${r.name}`}>
                      {r.pipeline}/{r.name}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {pipeline && version && (
              <div className="bg-gray-50 border border-gray-100 rounded-md px-2 py-1.5 text-xs">
                <span className="text-gray-500">Run: </span>
                <span className="font-mono text-gray-800">{pipeline}/{version}</span>
                <button
                  onClick={() => { setPipeline(''); setVersion('') }}
                  className="ml-2 text-gray-400 hover:text-gray-600"
                >
                  ×
                </button>
              </div>
            )}

            <button
              onClick={handleStart}
              disabled={!canStart}
              className="w-full rounded-md bg-rose-500 px-3 py-1.5 text-xs font-semibold
                         text-white hover:bg-rose-600 disabled:opacity-40 transition-colors"
            >
              {loading ? 'Starting…' : 'Start Eval Session'}
            </button>
          </>
        )}

        {/* ─ ACTIVE phase ─ */}
        {phase === 'active' && (
          <>
            {/* Session info bar */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500 font-mono truncate max-w-[200px]">
                {pipeline}/{version}
              </span>
              <button
                onClick={() => { setPhase('idle'); setSessionId(''); setSampleRows([]) }}
                className="text-xs text-gray-400 hover:text-gray-600"
              >
                ↺ Change run
              </button>
            </div>

            {statusMsg && (
              <p className="text-xs text-gray-400 italic">{statusMsg}</p>
            )}

            {/* LLM model for judge */}
            <div>
              <p className="text-xs font-medium text-gray-700 mb-1">LLM judge model</p>
              <input
                type="text"
                value={llmModel}
                onChange={e => setLlmModel(e.target.value)}
                placeholder="e.g. qwen3:4b"
                className="w-full text-xs rounded-md border border-gray-300 px-2 py-1
                           bg-white text-gray-700 focus:border-rose-400 outline-none"
              />
            </div>

            {/* Sample rows table */}
            {hasRows && (
              <div className="overflow-x-auto nowheel rounded-md border border-gray-100">
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-100">
                      <th className="text-left text-gray-400 font-medium px-2 py-1.5 whitespace-nowrap">
                        doc_id
                      </th>
                      {sampleCols.map(c => (
                        <th key={c} className="text-left text-gray-400 font-medium px-2 py-1.5 whitespace-nowrap">
                          {c}
                        </th>
                      ))}
                      <th className="text-left text-gray-400 font-medium px-2 py-1.5">Label</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sampleRows.map((row, i) => {
                      const rowId = String(row.doc_id ?? i)
                      const humanLabel = labels[rowId]
                      const llmLabel = llmLabels[rowId]
                      return (
                        <tr key={rowId} className="border-b border-gray-50 hover:bg-gray-50">
                          <td className="px-2 py-1.5 font-mono text-gray-500 whitespace-nowrap">
                            {rowId.slice(0, 10)}
                          </td>
                          {sampleCols.map(c => (
                            <td key={c}
                                className="px-2 py-1.5 text-gray-700 max-w-[90px] truncate"
                                title={String(row[c] ?? '')}>
                              {String(row[c] ?? '—')}
                            </td>
                          ))}
                          <td className="px-2 py-1.5">
                            <div className="flex gap-1 items-center">
                              {labelOptions.map(lbl => (
                                <button
                                  key={lbl}
                                  onClick={() => handleLabel(rowId, lbl)}
                                  className={`text-xs px-1.5 py-0.5 rounded border font-medium transition-colors
                                    ${humanLabel === lbl
                                      ? (LABEL_COLORS[lbl] ?? 'bg-gray-600 text-white border-gray-600')
                                      : LABEL_DEFAULT
                                    }`}
                                >
                                  {lbl.slice(0, 3)}
                                </button>
                              ))}
                              {llmLabel && (
                                <span className="text-xs text-gray-400 font-mono ml-0.5"
                                      title={`LLM: ${llmLabel}`}>
                                  🤖{llmLabel.slice(0, 3)}
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {!hasRows && !loading && (
              <p className="text-xs text-gray-400 text-center py-2">No rows sampled yet</p>
            )}

            {/* Action buttons */}
            <div className="grid grid-cols-3 gap-1.5">
              <button
                onClick={handleNextSample}
                disabled={loading}
                className="rounded-md bg-rose-500 px-2 py-1.5 text-xs font-semibold
                           text-white hover:bg-rose-600 disabled:opacity-40 transition-colors"
              >
                Next 5
              </button>
              <button
                onClick={handleLLMJudge}
                disabled={loading || !hasRows || !llmModel}
                className="rounded-md bg-amber-500 px-2 py-1.5 text-xs font-semibold
                           text-white hover:bg-amber-600 disabled:opacity-40 transition-colors"
              >
                LLM Judge
              </button>
              <button
                onClick={handleStats}
                disabled={loading}
                className="rounded-md border border-gray-300 px-2 py-1.5 text-xs font-semibold
                           text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors"
              >
                Stats
              </button>
            </div>

            {/* Agreement stats */}
            {stats && stats.n_compared > 0 && (
              <div className="bg-rose-50 border border-rose-100 rounded-md px-2.5 py-2 space-y-1">
                <p className="text-xs font-medium text-rose-700 mb-0.5">Agreement</p>
                {[
                  ['Compared',    stats.n_compared],
                  ['Exact match', `${Math.round((stats.exact_match_rate ?? 0) * 100)}%`],
                  ["Cohen's κ",   stats.cohens_kappa?.toFixed(2) ?? '—'],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between text-xs">
                    <span className="text-rose-600">{label}</span>
                    <span className="font-mono text-rose-800">{value}</span>
                  </div>
                ))}
              </div>
            )}
            {stats && stats.n_compared === 0 && (
              <p className="text-xs text-gray-400 text-center">
                Label some rows + run LLM judge to see agreement stats
              </p>
            )}
          </>
        )}

        {loading && (
          <p className="text-xs text-rose-400 italic text-center">Working…</p>
        )}

        {error && (
          <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded px-2 py-1.5">
            {error}
          </p>
        )}
      </div>

      <Handle type="target" position={Position.Left} id="results-in" style={{ top: '50%' }} />
    </div>
  )
}
