import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDraftPersistence, type DraftSnapshot } from '../src/draftPersistence'

type Payload = { text: string }
const instances: ReturnType<typeof createDraftPersistence<Payload>>[] = []
let values: Map<string, string>
function make(send = vi.fn(async (_item: DraftSnapshot<Payload>, _keepalive: boolean) => {})) {
  const instance = createDraftPersistence<Payload>({ key: scope => `draft:${scope}`, send })
  instances.push(instance)
  return instance
}
beforeEach(() => {
  vi.useFakeTimers()
  values = new Map()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  })
  vi.stubGlobal('window', new EventTarget())
})
afterEach(async () => {
  for (const instance of instances.splice(0)) instance.dispose()
  await Promise.resolve()
  vi.useRealTimers(); vi.unstubAllGlobals()
})

describe('shared draft durability', () => {
  it('keeps this page pending draft when another tab writes a newer journal', () => {
    const drafts = make()
    drafts.stage('paper', 1, { text: 'this page' })
    values.set('draft:paper', JSON.stringify({ scope: 'paper', revision: 2, value: { text: 'other page' } }))
    expect(drafts.read('paper', true)?.value.text).toBe('this page')
    expect(drafts.read('paper')?.value.text).toBe('other page')
  })
  it('journals the last keystroke before the debounce and reads it after recreation', () => {
    const send = vi.fn(async () => {})
    const first = make(send)
    first.stage('paper', 1, { text: '最后一次输入' })
    expect(send).not.toHaveBeenCalled()
    expect(JSON.parse(values.get('draft:paper')!).value.text).toBe('最后一次输入')
    expect(make().read('paper')?.value.text).toBe('最后一次输入')
  })

  it('keeps failed writes locally and retries after the connection recovers', async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined)
    const drafts = make(send)
    drafts.stage('space:step', 7, { text: '保留作答' })
    await vi.advanceTimersByTimeAsync(350)
    expect(values.has('draft:space:step')).toBe(true)
    await vi.advanceTimersByTimeAsync(5000)
    expect(send).toHaveBeenCalledTimes(2)
    expect(values.has('draft:space:step')).toBe(false)
    expect(drafts.read('space:step')?.revision).toBe(7)
  })

  it('an old acknowledgement cannot erase the journal of a newer edit', async () => {
    let acknowledgeOld!: () => void
    const old = new Promise<void>(resolve => { acknowledgeOld = resolve })
    const send = vi.fn().mockReturnValueOnce(old).mockRejectedValueOnce(new Error('offline'))
    const drafts = make(send)
    drafts.stage('paper', 1, { text: 'old' })
    await vi.advanceTimersByTimeAsync(350)
    drafts.stage('paper', 2, { text: 'new' })
    await vi.advanceTimersByTimeAsync(350)
    acknowledgeOld()
    await Promise.resolve(); await Promise.resolve()
    expect(JSON.parse(values.get('draft:paper')!).revision).toBe(2)
    expect(drafts.read('paper')?.value.text).toBe('new')
    send.mockResolvedValue(undefined)
  })

  it('pagehide flushes pending input with keepalive before the timer fires', async () => {
    const send = vi.fn(async () => {})
    const drafts = make(send)
    drafts.stage('paper', 4, { text: 'before refresh' })
    window.dispatchEvent(new Event('pagehide'))
    expect(send).toHaveBeenCalledWith(expect.objectContaining({ revision: 4 }), true)
    await vi.advanceTimersByTimeAsync(350)
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('switching scopes retains unsynchronized edits for the old scope', async () => {
    const send = vi.fn().mockRejectedValue(new Error('offline'))
    const drafts = make(send)
    drafts.stage('old-space:step', 8, { text: 'old course draft' })
    drafts.stage('new-space:step', 9, { text: 'new course draft' })
    await vi.advanceTimersByTimeAsync(350)
    expect(drafts.read('old-space:step')?.value.text).toBe('old course draft')
    expect(drafts.read('new-space:step')?.value.text).toBe('new course draft')
    send.mockResolvedValue(undefined)
  })
})
