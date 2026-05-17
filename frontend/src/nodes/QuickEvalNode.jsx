/**
 * QuickEvalNode — "Quick Eval"
 *
 * Phase 1: visual placeholder. Shows 5-sample alignment check summary.
 * Input handle only (results flow in, nothing flows out).
 */

import { Handle, Position } from 'reactflow'

export default function QuickEvalNode({ data, selected }) {
  const hasSamples = data.samples?.length > 0

  return (
    <div className={`w-60 rounded-xl border bg-white shadow-sm overflow-hidden
                     transition-shadow ${selected ? 'shadow-md' : 'hover:shadow-md'}`}>

      {/* Header */}
      <div className="bg-amber-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base">🔍</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Quick Eval</p>
          <p className="text-amber-100 text-xs leading-tight mt-0.5">Tasting</p>
        </div>
        <span className="text-white text-xs bg-amber-400 px-1.5 py-0.5 rounded-full">5</span>
      </div>

      {/* Body */}
      <div className="px-3 py-3 space-y-2">
        {hasSamples ? (
          <div className="space-y-1">
            {data.samples.map((s, i) => (
              <div key={i}
                className="flex items-center justify-between text-xs bg-gray-50
                           rounded px-2 py-1">
                <span className="text-gray-600 font-mono truncate max-w-[120px]">{s.id}</span>
                <span className={`px-1.5 py-0.5 rounded-full font-medium ${
                  s.label === 'good' ? 'bg-emerald-100 text-emerald-700' :
                  s.label === 'bad'  ? 'bg-red-100 text-red-600' :
                                       'bg-gray-100 text-gray-500'
                }`}>{s.label}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-2">
            <p className="text-xs text-gray-400">No results yet</p>
            <p className="text-xs text-gray-300 mt-0.5">Connect a Run block first</p>
          </div>
        )}

        {/* Eval button — wired in Phase 3 */}
        <button
          disabled
          className="w-full rounded-md bg-amber-500 px-3 py-1.5 text-xs font-semibold
                     text-white opacity-40 cursor-not-allowed"
        >
          Run 5-sample eval
        </button>
      </div>

      {/* Input handle */}
      <Handle type="target" position={Position.Left} id="results-in" style={{ top: '50%' }} />
    </div>
  )
}
