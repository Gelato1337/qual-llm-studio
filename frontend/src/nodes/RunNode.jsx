/**
 * RunNode — "Run Pipeline"
 *
 * Phase 1: visual placeholder. Shows model, row count, run button.
 * Input handle (from recipe) + output handle (results to eval).
 */

import { Handle, Position } from 'reactflow'

const STATUS_STYLES = {
  idle:    'bg-gray-100 text-gray-500',
  running: 'bg-amber-50 text-amber-600 border border-amber-200',
  done:    'bg-emerald-50 text-emerald-700 border border-emerald-200',
  failed:  'bg-red-50 text-red-600 border border-red-200',
}

export default function RunNode({ data, selected }) {
  const status = data.status || 'idle'
  const hasResults = status === 'done' && data.nOutputRows > 0

  return (
    <div className={`w-60 rounded-xl border bg-white shadow-sm overflow-hidden
                     transition-shadow ${selected ? 'shadow-md' : 'hover:shadow-md'}`}>

      {/* Header */}
      <div className="bg-emerald-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base">▶</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Run Pipeline</p>
          <p className="text-emerald-100 text-xs leading-tight mt-0.5">Cooking</p>
        </div>
      </div>

      {/* Body */}
      <div className="px-3 py-3 space-y-2.5">

        {/* Config row */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="bg-gray-50 rounded-md px-2 py-1.5">
            <p className="text-gray-400 text-xs leading-none mb-0.5">Model</p>
            <p className="font-mono text-gray-700 truncate">
              {data.model || <span className="text-gray-300">none</span>}
            </p>
          </div>
          <div className="bg-gray-50 rounded-md px-2 py-1.5">
            <p className="text-gray-400 text-xs leading-none mb-0.5">Rows</p>
            <p className="font-mono text-gray-700">
              {data.nRows > 0 ? data.nRows : <span className="text-gray-300">—</span>}
            </p>
          </div>
        </div>

        {/* Status pill */}
        <div className={`text-xs rounded-md px-2 py-1 font-medium text-center ${STATUS_STYLES[status]}`}>
          {status === 'idle'    && 'Ready to run'}
          {status === 'running' && `Running… ${data.progress || ''}`}
          {status === 'done'    && `Done — ${data.nOutputRows} rows`}
          {status === 'failed'  && (data.error || 'Failed')}
        </div>

        {/* Run button — wired up in Phase 3 */}
        <button
          disabled
          className="w-full rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold
                     text-white opacity-40 cursor-not-allowed"
        >
          Run
        </button>
      </div>

      {/* Handles */}
      <Handle type="target" position={Position.Left} id="recipe-in" style={{ top: '50%' }} />
      <Handle type="source" position={Position.Right} id="results-out" style={{ top: '50%' }} />
    </div>
  )
}
