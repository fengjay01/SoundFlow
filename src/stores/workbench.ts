import { defineStore } from 'pinia'
import { reactive } from 'vue'
import { createWorkflowBindings, type WorkflowRuntimeBindings } from '../workflows/workbench.ts'
import type { SeparationJob } from './task.ts'
import type { WorkbenchState } from '../workflows/workbenchBridge.ts'

export type WorkflowWorkbenchSession = {
  bindings: WorkflowRuntimeBindings
  viewMode: 'edit' | 'execution'
  selectedJobId: string
  selectedTaskId: string
}
export const useWorkbenchStore = defineStore('workbench', () => {
  const sessions = reactive<Record<string, WorkflowWorkbenchSession>>({})
  const remoteJobs = reactive<Record<string, SeparationJob[]>>({})
  function session(key: string) {
    if (!sessions[key]) sessions[key] = { bindings: createWorkflowBindings(), viewMode: 'edit', selectedJobId: '', selectedTaskId: '' }
    return sessions[key]
  }
  function adopt(from: string, to: string) {
    if (from !== to && sessions[from]) {
      sessions[to] = sessions[from]
      delete sessions[from]
    }
  }
  function applyRemoteState(state: WorkbenchState, preserveInputs = false) {
    remoteJobs[state.workflowKey] = state.jobs
    const target = session(state.workflowKey)
    if (state.session) {
      const { inputs, primaryInput } = target.bindings
      Object.assign(target, state.session)
      if (preserveInputs) target.bindings = { ...target.bindings, inputs, primaryInput }
    }
    if (!target.selectedJobId && state.jobs.length) target.selectedJobId = state.jobs[0].id
  }
  return { sessions, remoteJobs, session, adopt, applyRemoteState }
})
