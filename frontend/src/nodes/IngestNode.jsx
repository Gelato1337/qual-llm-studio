/**
 * IngestNode — "Load Data"
 *
 * Phase 1: visual placeholder. Accepts file drop / source selection UI later.
 * Output handle only (data flows right).
 */

import { Handle, Position } from 'reactflow'

const FILE_TYPES = ['CSV', 'XLSX', 'PDF', 'DOCX', 'TXT', 'JSON']

export default function IngestNode({ data, selected }) {
  const loaded = data.nRows > 0

  return (
    <div className={`w-60 rounded-xl border bg-white shadow-sm overflow-hidden
                     transition-shadow ${selected ? 'shadow-md' : 'hover:shadow-md'}`}>

      {/* Header */}
      <div className="bg-blue-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base">📂</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Load Data</p>
          <p className="text-blue-100 text-xs leading-tight mt-0.5">Ingredients</p>
        </div>
        {loaded && (
          <span className="bg-blue-400 text-white text-xs px-1.5 py-0.5 rounded-full font-mono">
            {data.nRows}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="px-3 py-3 space-y-2.5">
        {loaded ? (
          <div className="text-xs text-gray-600">
            <p className="font-medium text-gray-900">{data.sourceName}</p>
            <p className="text-gray-500">{data.nRows} rows · {data.nCols} columns</p>
          </div>
        ) : (
          <div className="border-2 border-dashed border-gray-200 rounded-lg px-3 py-3 text-center">
            <p className="text-xs text-gray-400">Drop a file here</p>
            <p className="text-xs text-gray-300 mt-0.5">or configure in the panel</p>
          </div>
        )}

        {/* Supported formats */}
        <div className="flex flex-wrap gap-1">
          {FILE_TYPES.map(t => (
            <span key={t}
              className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-mono">
              {t}
            </span>
          ))}
        </div>
      </div>

      {/* Output handle */}
      <Handle
        type="source"
        position={Position.Right}
        id="data-out"
        style={{ top: '50%' }}
      />
    </div>
  )
}
