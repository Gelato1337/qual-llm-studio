/**
 * RunNode — "Run Pipeline"
 *
 * Reads the recipe name from the connected RecipeNode (graph traversal).
 * Fetches available models from Ollama. Fires a run via POST /api/execute
 * and polls GET /api/execute/{job_id} until the job completes.
 *
 * Calls:
 *   GET  /api/workspace          → current host + model defaults
 *   GET  /api/ollama/models      → model list
 *   POST /api/execute            → start run → { job_id }
 *   GET  /api/execute/{job_id}   → poll status
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Handle, Position, useEdges, useNodes, useReactFlow } from 'reactflow'
import { getWorkspace, getModels, startRun, getJob } from '../lib/api.js'

const STATUS_PILL = {
  idle:    'bg-gray-100 text-gray-500',
  pending: 'bg-amber-50 text-amber-600 border border-amber-200',
  running: 'bg-amber-50 text-amber-600 border border-amber-200',
  done:    'bg-emerald-50 text-emerald-700 border border-emerald-200',
  failed:  'bg-red-50 text-red-600 border border-red-200',
}

export default function RunNode({ id, data }) {
  const { setNodes }   = useReactFlow()
  const allNodes       = useNodes()
  const allEdges       = useEdges()

  // Read recipe name from connected RecipeNode (upstream via input edge)
  const upstreamEdge = allEdges.find(e => e.target === id)
  const upstreamNode = allNodes.find(n => n.id === upstreamEdge?.source)
  const derivedRecipe = upstreamNode?.data?.recipeName ?? ''

  const [host,       setHost]      = useState('')
  const [models,     setModels]    = useState([])
  const [model,      setModel]     = useState(data.model ?? '')
  const [nParallel,  setNParallel] = useState(data.nParallel  ?? 1)
  const [sampleN,    setSampleN]   = useState(data.sampleN    ?? 0)
  const [status,     setStatus]    = useState(data.jobStatus  ?? 'idle')
  const [jobId,      setJobId]     = useState(data.jobId      ?? '')
  const [nOutputRows, setNOutputRows] = useState(data.nOutputRows ?? 0)
  const [runError,   setRunError]  = useState('')
  const [modelsErr,  setModelsErr] = useState('')

  const pollRef      = useRef(null)
  // Refs so the autoRun effect always sees the latest values without stale closures
  const handleRunRef = useRef(null)
  const canRunRef    = useRef(false)

  const updateData = useCallback((updates) => {
    setNodes(nds => nds.map(n =>
      n.id === id ? { ...n, data: { ...n.data, ...updates } } : n
    ))
  }, [id, setNodes])

  // Load workspace settings + model list once
  useEffect(() => {
    getWorkspace()
      .then(r => {
        const h = r.data.host
        const savedModel = r.data.model
        setHost(h)
        if (savedModel && !model) setModel(savedModel)
        return getModels(h)
      })
      .then(r => {
        const ms = r.data.models ?? []
        setModels(ms)
        if (!model && ms.length) setModel(ms[0])
      })
      .catch(() => setModelsErr('Could not reach Ollama'))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Polling loop — starts after a run is kicked off
  const startPolling = useCallback((jid) => {
    const tick = async () => {
      try {
        const { data: job } = await getJob(jid)
        setStatus(job.status)
        updateData({ jobStatus: job.status, jobId: jid })

        if (job.status === 'done') {
          setNOutputRows(job.n_output_rows ?? 0)
          updateData({ nOutputRows: job.n_output_rows ?? 0 })
        } else if (job.status === 'failed') {
          setRunError(job.error ?? 'Run failed')
        } else {
          pollRef.current = setTimeout(tick, 1500)
        }
      } catch (e) {
        setStatus('failed')
        setRunError(e.message)
      }
    }
    pollRef.current = setTimeout(tick, 1000)
  }, [updateData])

  // Clean up polling on unmount
  useEffect(() => () => clearTimeout(pollRef.current), [])

  const handleRun = async () => {
    const recipe = derivedRecipe || data.recipeName
    if (!recipe)  { setRunError('No recipe — connect a Recipe block'); return }
    if (!model)   { setRunError('No model selected'); return }

    setRunError('')
    setStatus('pending')

    try {
      const { data: res } = await startRun({
        recipe_name: recipe,
        host:        host || 'http://localhost:11434',
        model,
        n_parallel:  nParallel,
        sample_n:    sampleN,
      })
      setJobId(res.job_id)
      updateData({ jobId: res.job_id, jobStatus: 'pending' })
      startPolling(res.job_id)
    } catch (e) {
      setStatus('failed')
      setRunError(e.response?.data?.detail ?? e.message)
    }
  }

  const recipeName = derivedRecipe || data.recipeName || ''
  const canRun = recipeName && model && status !== 'pending' && status !== 'running'

  // Keep refs current every render so the autoRun effect has no stale closures
  canRunRef.current    = canRun
  handleRunRef.current = handleRun

  // Run All — parent sets data.autoRun = true to trigger this node
  useEffect(() => {
    if (!data.autoRun) return
    updateData({ autoRun: false })
    if (canRunRef.current) handleRunRef.current()
  }, [data.autoRun, updateData]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="w-60 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">

      {/* ── Header ── */}
      <div className="bg-emerald-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base leading-none">▶</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Run Pipeline</p>
          <p className="text-emerald-100 text-xs mt-0.5">Cooking</p>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="px-3 py-3 space-y-2.5 nodrag">

        {/* Recipe (read-only if derived from graph) */}
        <div className="text-xs">
          <p className="font-medium text-gray-700 mb-1">Recipe</p>
          {recipeName
            ? <p className="font-mono text-gray-800 bg-gray-50 border border-gray-100 rounded px-2 py-1 truncate">
                {recipeName}
              </p>
            : <p className="text-gray-400 italic">Connect a Recipe block</p>
          }
        </div>

        {/* Model */}
        <div>
          <p className="text-xs font-medium text-gray-700 mb-1">Model</p>
          {models.length > 0
            ? <select
                value={model}
                onChange={e => { setModel(e.target.value); updateData({ model: e.target.value }) }}
                className="w-full text-xs rounded-md border border-gray-300 px-2 py-1
                           bg-white text-gray-800 focus:border-emerald-400 outline-none nowheel"
              >
                {models.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            : <p className="text-xs text-gray-400 italic">
                {modelsErr || 'No models found'}
              </p>
          }
        </div>

        {/* Parallelism + sample */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <p className="text-xs font-medium text-gray-700 mb-1">Parallel</p>
            <input
              type="number" min={1} max={64} value={nParallel}
              onChange={e => setNParallel(Number(e.target.value))}
              className="w-full text-xs rounded-md border border-gray-300 px-2 py-1
                         bg-white text-gray-700 outline-none focus:border-emerald-400 nowheel"
            />
          </div>
          <div>
            <p className="text-xs font-medium text-gray-700 mb-1">
              Sample <span className="text-gray-400 font-normal">(0=all)</span>
            </p>
            <input
              type="number" min={0} value={sampleN}
              onChange={e => setSampleN(Number(e.target.value))}
              className="w-full text-xs rounded-md border border-gray-300 px-2 py-1
                         bg-white text-gray-700 outline-none focus:border-emerald-400 nowheel"
            />
          </div>
        </div>

        {/* Status pill */}
        {status !== 'idle' && (
          <div className={`text-xs rounded-md px-2 py-1.5 font-medium text-center ${STATUS_PILL[status]}`}>
            {status === 'pending' && 'Starting…'}
            {status === 'running' && 'Running…'}
            {status === 'done'    && `Done — ${nOutputRows} rows`}
            {status === 'failed'  && (runError || 'Failed')}
          </div>
        )}

        {/* Run button */}
        <button
          onClick={handleRun}
          disabled={!canRun}
          className="w-full rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold
                     text-white hover:bg-emerald-600 disabled:opacity-40 transition-colors"
        >
          {status === 'running' || status === 'pending' ? 'Running…' : 'Run ▶'}
        </button>

        {runError && status === 'idle' && (
          <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded px-2 py-1.5">
            {runError}
          </p>
        )}
      </div>

      <Handle type="target" position={Position.Left}  id="recipe-in"   style={{ top: '50%' }} />
      <Handle type="source" position={Position.Right} id="results-out" style={{ top: '50%' }} />
    </div>
  )
}
