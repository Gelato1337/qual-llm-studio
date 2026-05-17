/**
 * RecipeNode — "Recipe Builder"
 *
 * Phase 1: visual placeholder. Shows selected recipe name + prompt preview.
 * Input handle (from data) + output handle (recipe config to run).
 */

import { Handle, Position } from 'reactflow'

export default function RecipeNode({ data, selected }) {
  const hasRecipe = Boolean(data.recipeName)

  return (
    <div className={`w-64 rounded-xl border bg-white shadow-sm overflow-hidden
                     transition-shadow ${selected ? 'shadow-md' : 'hover:shadow-md'}`}>

      {/* Header */}
      <div className="bg-violet-500 px-3 py-2 flex items-center gap-2">
        <span className="text-base">📋</span>
        <div className="flex-1 min-w-0">
          <p className="text-white text-xs font-semibold leading-none">Recipe Builder</p>
          <p className="text-violet-200 text-xs leading-tight mt-0.5">Prompt Studio</p>
        </div>
      </div>

      {/* Body */}
      <div className="px-3 py-3 space-y-2">
        {hasRecipe ? (
          <>
            <p className="text-xs font-semibold text-gray-900">{data.recipeName}</p>
            {data.promptPreview && (
              <p className="text-xs text-gray-500 line-clamp-3 leading-relaxed bg-gray-50
                             rounded px-2 py-1.5 font-mono border border-gray-100">
                {data.promptPreview}
              </p>
            )}
            {data.outputFields?.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {data.outputFields.map(f => (
                  <span key={f}
                    className="text-xs px-1.5 py-0.5 rounded-full bg-violet-50
                               text-violet-600 border border-violet-100">
                    {f}
                  </span>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="text-center py-2">
            <p className="text-xs text-gray-400">No recipe selected</p>
            <p className="text-xs text-gray-300 mt-0.5">Connect to open the builder</p>
          </div>
        )}
      </div>

      {/* Handles */}
      <Handle type="target" position={Position.Left} id="data-in" style={{ top: '50%' }} />
      <Handle type="source" position={Position.Right} id="recipe-out" style={{ top: '50%' }} />
    </div>
  )
}
