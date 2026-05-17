/**
 * Block palette sidebar.
 *
 * Each block item is draggable onto the canvas. The `type` value in
 * dataTransfer must match a key in the nodeTypes map (see lib/nodeTypes.js).
 */

const BLOCK_GROUPS = [
  {
    label: 'Data',
    blocks: [
      {
        type: 'ingest',
        label: 'Load Data',
        description: 'Upload files or connect a dataset',
        icon: '📂',
        color: 'blue',
      },
    ],
  },
  {
    label: 'Recipe',
    blocks: [
      {
        type: 'recipe',
        label: 'Recipe Builder',
        description: 'Define your prompt and output schema',
        icon: '📋',
        color: 'violet',
      },
    ],
  },
  {
    label: 'Pipeline',
    blocks: [
      {
        type: 'run',
        label: 'Run Pipeline',
        description: 'Execute a recipe over your data',
        icon: '▶',
        color: 'emerald',
      },
    ],
  },
  {
    label: 'Evaluate',
    blocks: [
      {
        type: 'quickeval',
        label: 'Quick Eval',
        description: '5-sample alignment check',
        icon: '🔍',
        color: 'amber',
      },
      {
        type: 'eval',
        label: 'Full Eval',
        description: 'Devset / testset with LLM judge',
        icon: '📊',
        color: 'rose',
      },
    ],
  },
]

const COLOR_MAP = {
  blue:    'bg-blue-50 border-blue-100 text-blue-600',
  violet:  'bg-violet-50 border-violet-100 text-violet-600',
  emerald: 'bg-emerald-50 border-emerald-100 text-emerald-600',
  amber:   'bg-amber-50 border-amber-100 text-amber-600',
  rose:    'bg-rose-50 border-rose-100 text-rose-600',
}

function BlockItem({ block }) {
  const onDragStart = (e) => {
    e.dataTransfer.setData('application/reactflow', block.type)
    e.dataTransfer.effectAllowed = 'move'
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="group flex items-start gap-2.5 p-2.5 rounded-lg border border-gray-200
                 bg-white cursor-grab hover:border-indigo-200 hover:shadow-sm
                 active:cursor-grabbing transition-all select-none"
    >
      <div className={`w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0
                       border text-sm ${COLOR_MAP[block.color]}`}>
        {block.icon}
      </div>
      <div className="min-w-0">
        <p className="text-xs font-medium text-gray-800 leading-tight">{block.label}</p>
        <p className="text-xs text-gray-400 leading-tight mt-0.5">{block.description}</p>
      </div>
    </div>
  )
}

export default function Sidebar() {
  return (
    <aside className="w-56 flex-shrink-0 bg-white border-r border-gray-200
                      flex flex-col overflow-y-auto">
      <div className="px-3 py-3 border-b border-gray-100">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Blocks
        </p>
        <p className="text-xs text-gray-400 mt-0.5">Drag onto the canvas</p>
      </div>

      <div className="flex-1 px-3 py-3 space-y-4">
        {BLOCK_GROUPS.map(group => (
          <div key={group.label}>
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
              {group.label}
            </p>
            <div className="space-y-1.5">
              {group.blocks.map(block => (
                <BlockItem key={block.type} block={block} />
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Footer hint */}
      <div className="px-3 py-3 border-t border-gray-100">
        <p className="text-xs text-gray-400 leading-relaxed">
          Connect blocks by dragging from one handle to another.
        </p>
      </div>
    </aside>
  )
}
