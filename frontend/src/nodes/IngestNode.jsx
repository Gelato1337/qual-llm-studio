/**
 * IngestNode — "Load Data"
 *
 * Three stages:
 *   empty      → drop zone / click to browse
 *   loaded     → column picker + segmenter selector + Apply button
 *   structured → preview stats (n_rows, avg_chars, columns) + replace link
 *
 * Calls:
 *   POST /api/ingest/files      → source summary + column list
 *   POST /api/ingest/structure  → structured docs preview
 */

import { useCallback, useRef, useState } from 'react'
import { Handle, Position, useReactFlow } from 'reactflow'
import { uploadFiles, structureSources } from '../lib/api.js'

const SEGMENTERS = [
  { value: 'none',       label: 'No segmentation' },
  { value: 'paragraph',  label: 'By paragraph' },
  { value: 'char_length', label: 'Fixed length' },
]

export default function IngestNode({ id, data }) {
  const { setNodes } = useReactFlow()

  const [stage,    setStage]    = useState(data.stage    || 'empty')
  const [columns,  setColumns]  = useState(data.columns  || [])
  const [textCols, setTextCols] = useState(data.textCols || [])
  const [idCol,    setIdCol]    = useState(data.idCol    || '')
  const [segmenter, setSegmenter] = useState('none')
  const [chunkSize, setChunkSize] = useState(1000)
  const [preview,  setPreview]  = useState(data.preview  || null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')

  const fileRef = useRef()

  const updateData = useCallback((updates) => {
    setNodes(nds => nds.map(n =>
      n.id === id ? { ...n, data: { ...n.data, ...updates } } : n
    ))
  }, [id, setNodes])

  // ---- Stage 1: upload files ----
  const handleFiles = async (files) => {
    if (!files.length) return
    setLoading(true)
    setError('')
    try {
      const fd = new FormData()
      for (const f of files) fd.append('files', f)
      const { data: res } = await uploadFiles(fd)

      // Collect all columns from tabular sources
      const allCols = [...new Set(res.sources.flatMap(s => s.columns ?? []))]
      setColumns(allCols)
      // Pre-select first column as text column
      const defaultText = allCols.length ? [allCols[0]] : []
      setTextCols(defaultText)
      setIdCol('')
      setStage('loaded')
      updateData({ stage: 'loaded', columns: allCols, textCols: defaultText })
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }

  // ---- Stage 2: apply structure + segmentation ----
  const handleApply = async () => {
    if (!textCols.length) return
    setLoading(true)
    setError('')
    try {
      const params = segmenter === 'char_length' ? { chunk_size: chunkSize } : {}
      const { data: res } = await structureSources({
        text_columns:      textCols,
        id_column:         idCol || null,
        metadata_columns:  [],
        segmenter,
        segmenter_params:  params,
      })
      setPreview(res)
      setStage('structured')
      updateData({ stage: 'structured', preview: res, nRows: res.n_rows })
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }

  const toggleTextCol = (col) =>
    setTextCols(prev =>
      prev.includes(col) ? prev.filter(c => c !== col) : [...prev, col]
    )

  const reset = () => {
    setStage('empty')
    setColumns([])
    setTextCols([])
    setIdCol('')
    setPreview(null)
    setError('')
    updateData({ stage: 'empty', columns: [], textCols: [], preview: null, nRows: 0 })
  }

  return (
    <div className="w-64 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">

      {/* ── Header ── */}
      <div className="bg-blue-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base leading-none">📂</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Load Data</p>
          <p className="text-blue-100 text-xs mt-0.5">Ingredients</p>
        </div>
        {preview && (
          <span className="bg-blue-400 text-white text-xs px-1.5 py-0.5 rounded-full font-mono leading-none">
            {preview.n_rows}
          </span>
        )}
      </div>

      {/* ── Body ── */}
      <div className="px-3 py-3 space-y-2.5 nodrag">

        {/* Stage: empty — drop zone */}
        {stage === 'empty' && (
          <div
            className="border-2 border-dashed border-gray-200 rounded-lg px-3 py-4
                       text-center cursor-pointer transition-colors
                       hover:border-blue-300 hover:bg-blue-50"
            onClick={() => fileRef.current?.click()}
            onDragOver={e => { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'copy' }}
            onDrop={e => { e.preventDefault(); e.stopPropagation(); handleFiles([...e.dataTransfer.files]) }}
          >
            {loading
              ? <p className="text-xs text-blue-500 font-medium">Uploading…</p>
              : <>
                  <p className="text-xs font-medium text-gray-500">Drop a file or click to browse</p>
                  <p className="text-xs text-gray-400 mt-1">CSV · XLSX · PDF · DOCX · TXT · JSON</p>
                </>
            }
          </div>
        )}

        {/* Stage: loaded — column picker */}
        {stage === 'loaded' && (
          <>
            {/* Text columns */}
            <div>
              <p className="text-xs font-medium text-gray-700 mb-1.5">
                Text columns
                <span className="ml-1 text-gray-400 font-normal">(pick one or more)</span>
              </p>
              <div className="flex flex-wrap gap-1 max-h-24 overflow-y-auto nowheel pr-0.5">
                {columns.length > 0
                  ? columns.map(col => (
                      <button
                        key={col}
                        onClick={() => toggleTextCol(col)}
                        className={`text-xs px-2 py-0.5 rounded-full border transition-colors leading-snug ${
                          textCols.includes(col)
                            ? 'bg-blue-500 text-white border-blue-500'
                            : 'bg-white text-gray-600 border-gray-300 hover:border-blue-300'
                        }`}
                      >
                        {col}
                      </button>
                    ))
                  : <p className="text-xs text-gray-400 italic">No tabular columns detected</p>
                }
              </div>
            </div>

            {/* ID column */}
            {columns.length > 0 && (
              <div>
                <p className="text-xs font-medium text-gray-700 mb-1">
                  ID column <span className="text-gray-400 font-normal">(optional)</span>
                </p>
                <select
                  value={idCol}
                  onChange={e => setIdCol(e.target.value)}
                  className="w-full text-xs rounded-md border border-gray-300 px-2 py-1
                             bg-white text-gray-700 focus:border-blue-400 outline-none nowheel"
                >
                  <option value="">— none —</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            )}

            {/* Segmenter */}
            <div>
              <p className="text-xs font-medium text-gray-700 mb-1">Segmenter</p>
              <select
                value={segmenter}
                onChange={e => setSegmenter(e.target.value)}
                className="w-full text-xs rounded-md border border-gray-300 px-2 py-1
                           bg-white text-gray-700 focus:border-blue-400 outline-none nowheel"
              >
                {SEGMENTERS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>

            {segmenter === 'char_length' && (
              <div>
                <p className="text-xs font-medium text-gray-700 mb-1">Chunk size (chars)</p>
                <input
                  type="number"
                  min={100}
                  max={8000}
                  value={chunkSize}
                  onChange={e => setChunkSize(Number(e.target.value))}
                  className="w-full text-xs rounded-md border border-gray-300 px-2 py-1
                             bg-white text-gray-700 focus:border-blue-400 outline-none nowheel"
                />
              </div>
            )}

            <button
              onClick={handleApply}
              disabled={loading || (!textCols.length && columns.length > 0)}
              className="w-full rounded-md bg-blue-500 px-3 py-1.5 text-xs font-semibold
                         text-white hover:bg-blue-600 disabled:opacity-40 transition-colors"
            >
              {loading ? 'Applying…' : 'Apply structure'}
            </button>
          </>
        )}

        {/* Stage: structured — preview */}
        {stage === 'structured' && preview && (
          <>
            <div className="bg-blue-50 border border-blue-100 rounded-lg px-2.5 py-2 space-y-1">
              {[
                ['Rows',      preview.n_rows],
                ['Avg chars', preview.avg_chars],
                ['Min / Max', `${preview.min_chars} / ${preview.max_chars}`],
                ['Columns',   preview.columns?.join(', ')],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between text-xs">
                  <span className="text-gray-500">{label}</span>
                  <span className="font-mono text-gray-800 text-right max-w-[140px] truncate">{value}</span>
                </div>
              ))}
            </div>
            <button
              onClick={reset}
              className="w-full text-xs text-gray-400 hover:text-gray-600 transition-colors py-0.5"
            >
              ↺ Replace data
            </button>
          </>
        )}

        {error && (
          <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded px-2 py-1.5 leading-snug">
            {error}
          </p>
        )}
      </div>

      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        accept=".csv,.tsv,.xlsx,.xls,.pdf,.docx,.txt,.json,.jsonl"
        onChange={e => handleFiles([...e.target.files])}
      />

      <Handle type="source" position={Position.Right} id="data-out" style={{ top: '50%' }} />
    </div>
  )
}
