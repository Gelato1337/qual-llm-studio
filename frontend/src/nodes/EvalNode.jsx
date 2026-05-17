/**
 * EvalNode — "Full Eval"
 *
 * Phase 1: visual placeholder. Devset/testset with LLM judge.
 * Input handle only.
 */

import { Handle, Position } from 'reactflow'

export default function EvalNode({ data, selected }) {
  return (
    <div className={`w-64 rounded-xl border bg-white shadow-sm overflow-hidden
                     transition-shadow ${selected ? 'shadow-md' : 'hover:shadow-md'}`}>

      {/* Header */}
      <div className="bg-rose-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base">📊</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Full Eval</p>
          <p className="text-rose-200 text-xs leading-tight mt-0.5">Journal</p>
        </div>
      </div>

      {/* Body */}
      <div className="px-3 py-3 space-y-2.5">

        {/* Partition indicators */}
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-gray-50 rounded-md px-2 py-1.5 text-xs">
            <p className="text-gray-400 leading-none mb-0.5">Dev set</p>
            <p className="font-mono text-gray-700">
              {data.devCount ?? <span className="text-gray-300">—</span>}
            </p>
          </div>
          <div className="bg-gray-50 rounded-md px-2 py-1.5 text-xs">
            <p className="text-gray-400 leading-none mb-0.5">Test set</p>
            <p className="font-mono text-gray-700">
              {data.testCount ?? <span className="text-gray-300">—</span>}
            </p>
          </div>
        </div>

        {/* Agreement stats */}
        {data.kappa != null && (
          <div className="bg-rose-50 rounded-md px-2 py-1.5 text-xs border border-rose-100">
            <p className="text-rose-600 font-medium">
              κ = {data.kappa.toFixed(2)} · {Math.round(data.exactMatch * 100)}% exact
            </p>
          </div>
        )}

        {data.kappa == null && (
          <div className="text-center py-1">
            <p className="text-xs text-gray-400">No evaluations yet</p>
          </div>
        )}

        <button
          disabled
          className="w-full rounded-md bg-rose-500 px-3 py-1.5 text-xs font-semibold
                     text-white opacity-40 cursor-not-allowed"
        >
          Start eval session
        </button>
      </div>

      {/* Input handle */}
      <Handle type="target" position={Position.Left} id="results-in" style={{ top: '50%' }} />
    </div>
  )
}
