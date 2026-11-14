import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  isInterruptedTaskStatus,
  resolveJobStatus,
  selectQueuedJobGroups,
  sortJobTasks,
  type TaskLifecycleItem,
  type TaskStatus,
} from '../src/features/tasks/lifecycle.ts'

function task(id: string, status: TaskStatus, jobId = id, createdAt = 1): TaskLifecycleItem {
  return { id, jobId, status, createdAt }
}

describe('task job lifecycle', () => {
  it('keeps queued work resumable while marking active stages interrupted', () => {
    assert.equal(isInterruptedTaskStatus('queued'), false)
    assert.equal(isInterruptedTaskStatus('separating'), true)
  })

  it('does not report queued jobs as running', () => {
    assert.equal(resolveJobStatus([task('a', 'queued'), task('b', 'queued')]), 'queued')
    assert.equal(resolveJobStatus([task('a', 'done'), task('b', 'queued')]), 'queued')
  })

  it('reports mixed terminal results without hiding failures or cancellations', () => {
    assert.equal(resolveJobStatus([task('a', 'done'), task('b', 'failed')]), 'failed')
    assert.equal(resolveJobStatus([task('a', 'done'), task('b', 'cancelled')]), 'cancelled')
    assert.equal(resolveJobStatus([task('a', 'failed'), task('b', 'cancelled')]), 'failed')
    assert.equal(resolveJobStatus([task('a', 'done'), task('b', 'done')]), 'done')
  })

  it('uses the most advanced active stage for a running job', () => {
    assert.equal(resolveJobStatus([task('a', 'preparing'), task('b', 'queued')]), 'preparing')
    assert.equal(resolveJobStatus([task('a', 'loading_model'), task('b', 'separating')]), 'separating')
    assert.equal(resolveJobStatus([task('a', 'separating'), task('b', 'writing_output')]), 'writing_output')
  })
})

describe('Worker-slot queue selection', () => {
  it('uses batch order despite clock changes and keeps stored tasks unchanged', () => {
    const tasks: TaskLifecycleItem[] = [
      { ...task('second', 'done', 'batch', 10), batchIndex: 1 },
      { ...task('first', 'done', 'batch', 20), batchIndex: 0 },
    ]
    assert.deepEqual(sortJobTasks(tasks).map(item => item.id), ['first', 'second'])
    assert.deepEqual(tasks.map(item => item.id), ['second', 'first'])
    delete tasks[0].batchIndex
    assert.deepEqual(sortJobTasks(tasks).map(item => item.id), ['second', 'first'])
  })

  it('counts a multi-task batch as one active Worker', () => {
    const tasks = [
      task('active-a', 'separating', 'active', 1),
      task('active-b', 'preparing', 'active', 2),
      task('next-a', 'queued', 'next', 3),
      task('next-b', 'queued', 'next', 4),
    ]

    assert.deepEqual(selectQueuedJobGroups(tasks, 1), [])
    assert.deepEqual(selectQueuedJobGroups(tasks, 2).map(group => group.map(item => item.id)), [
      ['next-a', 'next-b'],
    ])
  })

  it('selects complete queued jobs in submission order', () => {
    const tasks = [
      task('batch-a', 'queued', 'batch', 1),
      task('batch-b', 'queued', 'batch', 2),
      task('single', 'queued', 'single', 3),
      task('later', 'queued', 'later', 4),
    ]

    assert.deepEqual(selectQueuedJobGroups(tasks, 2).map(group => group.map(item => item.id)), [
      ['batch-a', 'batch-b'],
      ['single'],
    ])
  })

  it('uses explicit source order for tasks created in the same millisecond', () => {
    const tasks: TaskLifecycleItem[] = [
      { id: 'third', jobId: 'batch', batchIndex: 2, createdAt: 1, status: 'queued' },
      { id: 'second', jobId: 'batch', batchIndex: 1, createdAt: 1, status: 'queued' },
      { id: 'first', jobId: 'batch', batchIndex: 0, createdAt: 1, status: 'queued' },
    ]
    assert.deepEqual(selectQueuedJobGroups(tasks, 1).map(group => group.map(item => item.id)), [
      ['first', 'second', 'third'],
    ])
  })

  it('keeps jobs FIFO before sorting the complete batch internally', () => {
    const tasks: TaskLifecycleItem[] = [
      { id: 'newer-second', jobId: 'newer', batchIndex: 1, createdAt: 20, status: 'queued' },
      { id: 'older-second', jobId: 'older', batchIndex: 1, createdAt: 10, status: 'queued' },
      { id: 'newer-first', jobId: 'newer', batchIndex: 0, createdAt: 20, status: 'queued' },
      { id: 'older-first', jobId: 'older', batchIndex: 0, createdAt: 10, status: 'queued' },
    ]
    assert.deepEqual(selectQueuedJobGroups(tasks, 2).map(group => group.map(item => item.id)), [
      ['older-first', 'older-second'],
      ['newer-first', 'newer-second'],
    ])
  })

  it('keeps legacy partial batch metadata in stable creation order', () => {
    const tasks: TaskLifecycleItem[] = [
      { id: 'legacy-first', jobId: 'legacy', createdAt: 1, status: 'queued' },
      { id: 'newer-indexed', jobId: 'legacy', batchIndex: 0, createdAt: 2, status: 'queued' },
    ]
    assert.deepEqual(selectQueuedJobGroups(tasks, 1).map(group => group.map(item => item.id)), [
      ['legacy-first', 'newer-indexed'],
    ])
  })

  it('does not start queued children of an already active job', () => {
    const tasks = [
      task('active', 'separating', 'batch', 1),
      task('same-job', 'queued', 'batch', 2),
      task('other', 'queued', 'other', 3),
    ]

    assert.deepEqual(selectQueuedJobGroups(tasks, 2).map(group => group.map(item => item.id)), [['other']])
  })
})
