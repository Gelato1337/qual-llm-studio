/**
 * React Flow nodeTypes registry.
 *
 * Every custom node must be registered here. The keys match the `type`
 * values used in sidebar drag-start and when creating nodes programmatically.
 *
 * Defined outside App.jsx so the object reference is stable across renders
 * (React Flow warns if nodeTypes changes on every render).
 */

import IngestNode    from '../nodes/IngestNode.jsx'
import RecipeNode    from '../nodes/RecipeNode.jsx'
import RunNode       from '../nodes/RunNode.jsx'
import QuickEvalNode from '../nodes/QuickEvalNode.jsx'
import EvalNode      from '../nodes/EvalNode.jsx'

const nodeTypes = {
  ingest:    IngestNode,
  recipe:    RecipeNode,
  run:       RunNode,
  quickeval: QuickEvalNode,
  eval:      EvalNode,
}

export default nodeTypes
