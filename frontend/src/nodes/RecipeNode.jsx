/**
 * RecipeNode — "Recipe Builder"
 *
 * Fetches the recipe list on mount. Selecting a recipe loads its full
 * details (prompt preview, output field tags). Stores `recipeName` in node
 * data so downstream nodes (RunNode) can read it via graph traversal.
 *
 * Calls:
 *   GET /api/recipes        → recipe stubs list
 *   GET /api/recipes/{name} → full recipe dict + text rendering
 */

import { useCallback, useEffect, useState } from 'react'
import { Handle, Position, useReactFlow } from 'reactflow'
import { getRecipes, getRecipe } from '../lib/api.js'

export default function RecipeNode({ id, data }) {
  const { setNodes } = useReactFlow()

  const [recipes,  setRecipes]  = useState([])          // stub list
  const [selected, setSelected] = useState(data.recipeName || '')
  const [detail,   setDetail]   = useState(null)        // full recipe dict
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')

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
    if (!selected) { setDetail(null); return }
    setLoading(true)
    setError('')
    getRecipe(selected)
      .then(r => {
        setDetail(r.data.recipe)
        updateData({
          recipeName:    selected,
          promptPreview: r.data.recipe.prompt?.slice(0, 120) ?? '',
          outputFields:  (r.data.recipe.output?.fields ?? []).map(f => f.name),
        })
      })
      .catch(e => setError(e.response?.data?.detail ?? e.message))
      .finally(() => setLoading(false))
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps

  const fields   = detail?.output?.fields ?? []
  const prompt   = detail?.prompt ?? ''
  const desc     = detail?.description ?? ''

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
            onChange={e => setSelected(e.target.value)}
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

            {/* Prompt preview */}
            {prompt && (
              <div className="bg-gray-50 border border-gray-100 rounded-md px-2.5 py-2">
                <p className="text-xs font-medium text-gray-500 mb-1">Prompt</p>
                <p className="text-xs text-gray-700 font-mono leading-relaxed line-clamp-4">
                  {prompt}
                </p>
              </div>
            )}

            {/* Output fields */}
            {fields.length > 0 && (
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

            {/* Tier badge */}
            {detail.tier > 1 && (
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
