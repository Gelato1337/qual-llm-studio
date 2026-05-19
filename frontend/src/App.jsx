import { useCallback, useEffect, useRef, useState } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  BackgroundVariant,
} from 'reactflow'

import nodeTypes from './lib/nodeTypes.js'
import Header from './components/Header.jsx'
import Sidebar from './components/Sidebar.jsx'
import SettingsPanel from './components/SettingsPanel.jsx'

// ---------------------------------------------------------------------------
// Canvas persistence — load once at module init, save on change
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'qlms_canvas_v1'

function loadSavedCanvas() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return null
}

const _saved = loadSavedCanvas()

// ---------------------------------------------------------------------------
// Default canvas — shown when no saved state exists
// ---------------------------------------------------------------------------

const INITIAL_NODES = [
  {
    id: 'demo-ingest',
    type: 'ingest',
    position: { x: 60, y: 180 },
    data: { nRows: 0 },
  },
  {
    id: 'demo-recipe',
    type: 'recipe',
    position: { x: 380, y: 160 },
    data: {
      recipeName: 'theme_grounded',
      promptPreview: 'Identify the main themes in the following text:\n\n{{text}}',
      outputFields: ['theme', 'evidence', 'confidence'],
    },
  },
  {
    id: 'demo-run',
    type: 'run',
    position: { x: 720, y: 180 },
    data: { status: 'idle', model: '', nRows: 0 },
  },
  {
    id: 'demo-eval',
    type: 'quickeval',
    position: { x: 1040, y: 180 },
    data: { samples: [] },
  },
]

const INITIAL_EDGES = [
  {
    id: 'e-ingest-recipe',
    source: 'demo-ingest',
    sourceHandle: 'data-out',
    target: 'demo-recipe',
    targetHandle: 'data-in',
    type: 'smoothstep',
  },
  {
    id: 'e-recipe-run',
    source: 'demo-recipe',
    sourceHandle: 'recipe-out',
    target: 'demo-run',
    targetHandle: 'recipe-in',
    type: 'smoothstep',
  },
  {
    id: 'e-run-eval',
    source: 'demo-run',
    sourceHandle: 'results-out',
    target: 'demo-eval',
    targetHandle: 'results-in',
    type: 'smoothstep',
  },
]

// ---------------------------------------------------------------------------
// Counter for unique node IDs when dropping from sidebar
// ---------------------------------------------------------------------------
let nodeIdCounter = 1
const nextId = (type) => `${type}-${nodeIdCounter++}`

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState(_saved?.nodes ?? INITIAL_NODES)
  const [edges, setEdges, onEdgesChange] = useEdgesState(_saved?.edges ?? INITIAL_EDGES)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [rfInstance, setRfInstance]     = useState(null)
  const wrapperRef  = useRef(null)
  const saveTimer   = useRef(null)
  const importRef   = useRef(null)

  // ── Persist canvas to localStorage (debounced 800 ms) ──────────────────────
  useEffect(() => {
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ nodes, edges }))
      } catch {}
    }, 800)
  }, [nodes, edges])

  // ── Connect two nodes ──────────────────────────────────────────────────────
  const onConnect = useCallback(
    (params) => setEdges((eds) => addEdge({ ...params, type: 'smoothstep' }, eds)),
    [setEdges],
  )

  // ── Drop from sidebar ──────────────────────────────────────────────────────
  const onDragOver = useCallback((e) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback((e) => {
    e.preventDefault()
    const type = e.dataTransfer.getData('application/reactflow')
    if (!type || !rfInstance) return

    const position = rfInstance.screenToFlowPosition({ x: e.clientX, y: e.clientY })
    setNodes((nds) => nds.concat({
      id: nextId(type),
      type,
      position,
      data: defaultData(type),
    }))
  }, [rfInstance, setNodes])

  // ── Export ─────────────────────────────────────────────────────────────────
  const handleExport = useCallback(() => {
    const payload = JSON.stringify({ nodes, edges }, null, 2)
    const blob = new Blob([payload], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `qlms-pipeline-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [nodes, edges])

  // ── Import ─────────────────────────────────────────────────────────────────
  const handleImportFile = useCallback((e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const { nodes: n, edges: eg } = JSON.parse(ev.target.result)
        setNodes(n ?? [])
        setEdges(eg ?? [])
        if (rfInstance) setTimeout(() => rfInstance.fitView({ padding: 0.2 }), 50)
      } catch {
        alert('Invalid pipeline file.')
      }
    }
    reader.readAsText(file)
    e.target.value = ''   // reset so the same file can be re-imported
  }, [rfInstance, setNodes, setEdges])

  // ── Run All — sets autoRun flag on every idle RunNode ─────────────────────
  const handleRunAll = useCallback(() => {
    setNodes(nds => nds.map(n =>
      n.type === 'run' ? { ...n, data: { ...n.data, autoRun: true } } : n
    ))
  }, [setNodes])

  // ── New canvas ─────────────────────────────────────────────────────────────
  const handleNew = useCallback(() => {
    if (!window.confirm('Start a new canvas? Unsaved changes will be lost.')) return
    localStorage.removeItem(STORAGE_KEY)
    setNodes(INITIAL_NODES)
    setEdges(INITIAL_EDGES)
    if (rfInstance) setTimeout(() => rfInstance.fitView({ padding: 0.2 }), 50)
  }, [rfInstance, setNodes, setEdges])

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-gray-50">
      <Header
        onSettingsToggle={() => setSettingsOpen(o => !o)}
        settingsOpen={settingsOpen}
        onExport={handleExport}
        onImport={() => importRef.current?.click()}
        onRunAll={handleRunAll}
        onNew={handleNew}
      />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar />

        {/* Canvas */}
        <div ref={wrapperRef} className="flex-1 relative">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onInit={setRfInstance}
            onDrop={onDrop}
            onDragOver={onDragOver}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            deleteKeyCode="Delete"
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#e5e7eb" />
            <Controls className="!bottom-4 !left-4" />
            <MiniMap
              nodeColor={miniMapColor}
              className="!bottom-4 !right-4 !border-gray-200 !rounded-lg"
              maskColor="rgba(249,250,251,0.7)"
            />
          </ReactFlow>

          {/* Empty-canvas hint */}
          {nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="text-center">
                <p className="text-2xl mb-2">🧱</p>
                <p className="text-sm font-medium text-gray-400">Drag blocks from the sidebar</p>
                <p className="text-xs text-gray-300 mt-1">Connect them to build a pipeline</p>
              </div>
            </div>
          )}
        </div>

        {/* Settings slide-over */}
        {settingsOpen && (
          <SettingsPanel onClose={() => setSettingsOpen(false)} />
        )}
      </div>

      {/* Hidden import file input */}
      <input
        ref={importRef}
        type="file"
        accept=".json"
        className="hidden"
        onChange={handleImportFile}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function defaultData(type) {
  switch (type) {
    case 'ingest':    return { nRows: 0 }
    case 'recipe':    return { recipeName: '', promptPreview: '', outputFields: [] }
    case 'run':       return { status: 'idle', model: '', nRows: 0 }
    case 'quickeval': return { samples: [] }
    case 'eval':      return { devCount: null, testCount: null, kappa: null }
    default:          return {}
  }
}

function miniMapColor(node) {
  switch (node.type) {
    case 'ingest':    return '#3b82f6'
    case 'recipe':    return '#8b5cf6'
    case 'run':       return '#10b981'
    case 'quickeval': return '#f59e0b'
    case 'eval':      return '#f43f5e'
    default:          return '#d1d5db'
  }
}
