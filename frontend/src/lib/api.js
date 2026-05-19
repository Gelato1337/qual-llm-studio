/**
 * Axios helpers for all /api/* endpoints.
 * Import individual functions rather than the default instance.
 */

import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export default api

// --- Workspace ---
export const getWorkspace   = ()     => api.get('/workspace')
export const updateSettings = (body) => api.put('/workspace/settings', body)

// --- Ollama ---
export const getOllamaStatus = (host) => api.get('/ollama/status', { params: { host } })
export const getModels       = (host) => api.get('/ollama/models',  { params: { host } })

// --- Ingest ---
export const uploadFiles      = (fd)     => api.post('/ingest/files',     fd)
export const pasteSources     = (body)   => api.post('/ingest/paste',     body)
export const structureSources = (config) => api.post('/ingest/structure', config)
export const getPreview       = (n = 10) => api.get('/ingest/preview',    { params: { n } })

// --- Recipes ---
export const getRecipes  = ()          => api.get('/recipes')
export const getRecipe   = (name)      => api.get(`/recipes/${encodeURIComponent(name)}`)
export const saveRecipe  = (name, body) => api.put(`/recipes/${encodeURIComponent(name)}`, body)

// --- Execute ---
export const startRun = (body)   => api.post('/execute',          body)
export const getJob   = (jobId)  => api.get(`/execute/${jobId}`)
export const listRuns = ()       => api.get('/execute/runs/list')

// --- Eval ---
export const createEvalSession = (pipeline, version) =>
  api.post('/eval/sessions', { pipeline, version })
export const sampleEval = (sid, n = 5) =>
  api.get(`/eval/sessions/${sid}/sample`, { params: { n } })
export const judgeHuman = (sid, row_id, label, comment = '') =>
  api.post(`/eval/sessions/${sid}/judge`, { row_id, label, comment })
export const judgeLLM = (sid, body) =>
  api.post(`/eval/sessions/${sid}/llm`, body)
export const evalStats = (sid) =>
  api.get(`/eval/sessions/${sid}/stats`)
