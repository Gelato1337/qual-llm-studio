/**
 * RecipeNode — "Recipe Builder"
 *
 * Fetches the recipe list on mount. Selecting a recipe loads its full
 * details (prompt preview, output field tags). Stores `recipeName` in node
 * data so downstream nodes (RunNode) can read it via graph traversal.
 *
 * Includes a collapsible JSON editor — click "Edit" to expand a textarea
 * pre-filled with the recipe JSON; "Save" calls PUT /api/recipes/{name}.
 *
 * Calls:
 *   GET /api/recipes        → recipe stubs list
 *   GET /api/recipes/{name} → full recipe dict + text rendering
 *   PUT /api/recipes/{name} → save edited recipe
 */

import { useCallback, useEffect, useState } from 'react'
import { Handle, Position, useReactFlow } from 'reactflow'
import { getRecipes, getRecipe, saveRecipe } from '../lib/api.js'

export default function RecipeNode({ id, data }) {
  const { setNodes } = useReactFlow()

  const [recipes,    setRecipes]    = useState([])
  const [selected,   setSelected]   = useState(data.recipeName || '')
  const [detail,     setDetail]     = useState(null)
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState('')
  const [editorOpen, setEditorOpen] = useState(false)
  const [jsonDraft,  setJsonDraft]  = useState('')
  const [saveStatus, setSaveStatus] = useState('')   // '' | 'saving' | 'saved' | 'error'

  const updateData = useCallback((updates) => {
    setNodes(nds => nds.map(n =>
      n.id === id ? { ...n, data: { ...n.data, ...updates } } : n
    ))
  }, [id, setNodes])

  // Fetch recipe list once on mount
  useEffect(() => {
    getRecipes()
      .then(r => setRecipes(r.data.recipes ?? []))
      .catch(() => setError('Could not load recipes'))
  }, [])

  // Fetch full details whenever selection changes
  useEffect(() => {
    if (!selected) { setDetail(null); setEditorOpen(false); return }
    setLoading(true)
    setError('')
    setSaveStatus('')
    getRecipe(selected)
      .then(r => {
        const rec = r.data.recipe
        setDetail(rec)
        setJsonDraft(JSON.stringify(rec, null, 2))
        updateData({
          recipeName:    selected,
          promptPreview: rec.prompt?.slice(0, 120) ?? '',
          outputFields:  (rec.output?.fields ?? []).map(f => f.name),
        })
      })
      .catch(e => setError(e.response?.data?.detail ?? e.message))
      .finally(() => setLoading(false))
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    let parsed
    try {
      parsed = JSON.parse(jsonDraft)
    } catch {
      setSaveStatus('error')
      setError('Invalid JSON')
      return
    }
    setSaveStatus('saving')
    setError('')
    try {
      await saveRecipe(selected, parsed)
      setDetail(parsed)
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus(''), 2000)
    } catch (e) {
      setError(e.response?.data?.detail ?? e.message)
      setSaveStatus('error')
    }
  }

  const fields = detail?.output?.fields ?? []
  const prompt = detail?.prompt ?? ''
  const desc   = detail?.description ?? ''

  return (
    <div className="w-64 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">

      {/* ── Header ── */}
      <div className="bg-violet-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base leading-none">📋</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Recipe Builder</p>
          <p className="text-violet-200 text-xs mt-0.5">Prompt Studio</p>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="px-3 py-3 space-y-2.5 nodrag">

        {/* Recipe picker */}
        <div>
          <p className="text-xs font-medium text-gray-700 mb-1">Recipe</p>
          <select
            value={selected}
            onChange={e => { setSelected(e.target.value); setEditorOpen(false) }}
            className="w-full text-xs rounded-md border border-gray-300 px-2 py-1.5
                       bg-white text-gray-800 focus:border-violet-400 outline-none nowheel"
          >
            <option value="">— choose a recipe —</option>
            {recipes.map(r => (
              <option key={r.name} value={r.name}>{r.name}</option>
            ))}
          </select>
        </div>

        {loading && (
          <p className="text-xs text-violet-400 italic">Loading…</p>
        )}

        {/* Recipe details */}
        {detail && !loading && (
          <>
            {desc && (
              <p className="text-xs text-gray-500 leading-relaxed">{desc}</p>
            )}

            {/* Prompt preview (hidden when editor is open) */}
            {prompt && !editorOpen && (
              <div className="bg-gray-50 border border-gray-100 rounded-md px-2.5 py-2">
                <p className="text-xs font-medium text-gray-500 mb-1">Prompt</p>
                <p className="text-xs text-gray-700 font-mono leading-relaxed line-clamp-4">
                  {prompt}
                </p>
              </div>
            )}

            {/* Output fields */}
            {fields.length > 0 && !editorOpen && (
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1">Output fields</p>
                <div className="flex flex-wrap gap-1">
                  {fields.map(f => (
                    <span
                      key={f.name}
                      title={`${f.type}${f.description ? ' — ' + f.description : ''}`}
                      className="text-xs px-1.5 py-0.5 rounded-full bg-violet-50
                                 text-violet-600 border border-violet-100"
                    >
                      {f.name}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* JSON editor */}
            {editorOpen && (
              <div className="space-y-1.5">
                <textarea
                  rows={10}
                  value={jsonDraft}
                  onChange={e => { setJsonDraft(e.target.value); setSaveStatus('') }}
                  spellCheck={false}
                  className="w-full text-xs font-mono rounded-md border border-gray-300
                             px-2 py-1.5 bg-gray-50 text-gray-800 focus:border-violet-400
                             outline-none resize-none nowheel"
                />
                <div className="flex gap-1.5">
                  <button
                    onClick={handleSave}
                    disabled={saveStatus === 'saving'}
                    className="flex-1 rounded-md bg-violet-500 px-2 py-1 text-xs font-semibold
                               text-white hover:bg-violet-600 disabled:opacity-40 transition-colors"
                  >
                    {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved ✓' : 'Save'}
                  </button>
                  <button
                    onClick={() => { setEditorOpen(false); setSaveStatus('') }}
                    className="rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-500
                               hover:bg-gray-50 transition-colors"
                  >
                    Close
                  </button>
                </div>
              </div>
            )}

            {/* Edit toggle */}
            {!editorOpen && (
              <button
                onClick={() => setEditorOpen(true)}
                className="w-full text-xs text-gray-400 hover:text-violet-500 transition-colors py-0.5"
              >
                ✏ Edit JSON
              </button>
            )}

            {/* Tier badge */}
            {!editorOpen && detail.tier > 1 && (
              <p className="text-xs text-gray-400">
                Tier {detail.tier} · {detail.type}
              </p>
            )}
          </>
        )}

        {!selected && !loading && (
          <p className="text-xs text-gray-400 text-center py-1">
            Select a recipe to see details
          </p>
        )}

        {error && (
          <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded px-2 py-1.5">
            {error}
          </p>
        )}
      </div>

      <Handle type="target" position={Position.Left}  id="data-in"    style={{ top: '50%' }} />
      <Handle type="source" position={Position.Right} id="recipe-out" style={{ top: '50%' }} />
    </div>
  )
}
