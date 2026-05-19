/**
 * QuickEvalNode — "Quick Eval"
 *
 * Reads `jobId` + `jobStatus` from the directly connected upstream node
 * (expected to be a RunNode) via React Flow graph traversal. When the
 * job status flips to 'done', fetches the first 5 result rows and
 * renders them as a compact table.
 *
 * Calls:
 *   GET /api/execute/{job_id}  → result rows
 */

import { useEffect, useState } from 'react'
import { Handle, Position, useEdges, useNodes } from 'reactflow'
import { getJob } from '../lib/api.js'

export default function QuickEvalNode({ id }) {
  const allNodes = useNodes()
  const allEdges = useEdges()

  // Find connected upstream RunNode
  const inEdge    = allEdges.find(e => e.target === id)
  const sourceNode = allNodes.find(n => n.id === inEdge?.source)
  const jobId     = sourceNode?.data?.jobId
  const jobStatus = sourceNode?.data?.jobStatus

  const [samples, setSamples] = useState([])
  const [columns, setColumns] = useState([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  // Re-fetch whenever the upstream job completes
  useEffect(() => {
    if (!jobId || jobStatus !== 'done') return
    setLoading(true)
    setError('')
    getJob(jobId)
      .then(r => {
        const rows = r.data.rows ?? []
        const cols = r.data.columns ?? []
        // Show only the most interesting columns (drop internal ones)
        const visibleCols = cols.filter(c => !c.startsWith('_')).slice(0, 4)
        setSamples(rows.slice(0, 5))
        setColumns(visibleCols)
      })
      .catch(e => setError(e.response?.data?.detail ?? e.message))
      .finally(() => setLoading(false))
  }, [jobId, jobStatus])

  const hasSamples = samples.length > 0

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden"
         style={{ width: hasSamples ? 420 : 240 }}>

      {/* ── Header ── */}
      <div className="bg-amber-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base leading-none">🔍</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Quick Eval</p>
          <p className="text-amber-100 text-xs mt-0.5">Tasting</p>
        </div>
        <span className="text-white text-xs bg-amber-400 px-1.5 py-0.5 rounded-full leading-none">
          5
        </span>
      </div>

      {/* ── Body ── */}
      <div className="px-3 py-3 nodrag">

        {loading && (
          <p className="text-xs text-amber-500 italic text-center py-2">Loading results…</p>
        )}

        {!loading && !hasSamples && !error && (
          <div className="text-center py-3">
            {jobStatus === 'done'
              ? <p className="text-xs text-gray-400">No results in this run</p>
              : jobId
                ? <p className="text-xs text-gray-400">Waiting for run to complete…</p>
                : <p className="text-xs text-gray-400">Connect a Run block to see samples</p>
            }
          </div>
        )}

        {/* Results mini-table */}
        {!loading && hasSamples && (
          <div className="overflow-x-auto nowheel">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left text-gray-400 font-medium py-1 pr-3 whitespace-nowrap">
                    doc_id
                  </th>
                  {columns.map(c => (
                    <th key={c}
                        className="text-left text-gray-400 font-medium py-1 pr-3 whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {samples.map((row, i) => (
                  <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="py-1 pr-3 font-mono text-gray-500 whitespace-nowrap">
                      {String(row.doc_id ?? i).slice(0, 12)}
                    </td>
                    {columns.map(c => (
                      <td key={c} className="py-1 pr-3 text-gray-700 max-w-[120px] truncate"
                          title={String(row[c] ?? '')}>
                        {String(row[c] ?? '—')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-gray-400 mt-1.5">
              Showing 5 of {sourceNode?.data?.nOutputRows ?? '?'} rows
            </p>
          </div>
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
