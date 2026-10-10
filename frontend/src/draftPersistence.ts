export interface DraftSnapshot<T> { scope: string; revision: number; value: T }
type SaveState = 'saving' | 'saved' | 'failed'
interface Options<T> {
  key: (scope: string) => string
  send: (snapshot: DraftSnapshot<T>, keepalive: boolean) => Promise<void>
  decode?: (value: unknown, scope: string) => DraftSnapshot<T> | null
  state?: (snapshot: DraftSnapshot<T>, state: SaveState, error?: unknown) => void
  retry?: (error: unknown) => boolean
}
interface Pending<T> { snapshot: DraftSnapshot<T>; timer?: ReturnType<typeof setTimeout>; retry?: ReturnType<typeof setTimeout> }

// Adapters own the payload and acknowledgement; both screens share persistence.
export function createDraftPersistence<T>(options: Options<T>) {
  const pending = new Map<string, Pending<T>>()
  const latest = new Map<string, DraftSnapshot<T>>()
  const flights = new Set<Promise<boolean>>()
  let disposed = false
  function read(scope: string): DraftSnapshot<T> | null {
    let stored: DraftSnapshot<T> | null = null
    try {
      const value = JSON.parse(localStorage.getItem(options.key(scope)) || 'null')
      stored = options.decode ? options.decode(value, scope) : value
      if (!stored || stored.scope !== scope || !Number.isSafeInteger(stored.revision) || stored.revision < 1 || !stored.value) stored = null
    } catch { /* Storage is optional; in-memory edits remain available. */ }
    const memory = latest.get(scope)
    return memory && (!stored || memory.revision >= stored.revision) ? memory : stored
  }
  function forget(scope: string) {
    const item = pending.get(scope)
    clearTimeout(item?.timer); clearTimeout(item?.retry)
    pending.delete(scope); latest.delete(scope)
    try { localStorage.removeItem(options.key(scope)) } catch { /* optional storage */ }
  }
  function send(item: Pending<T>, keepalive = false): Promise<boolean> {
    clearTimeout(item.timer); clearTimeout(item.retry)
    item.timer = undefined; item.retry = undefined
    const snapshot = item.snapshot
    const operation = (async () => {
      try {
        await options.send(snapshot, keepalive)
        if (pending.get(snapshot.scope) === item) {
          pending.delete(snapshot.scope)
          // A later keystroke or another tab may have written a newer journal.
          if (read(snapshot.scope)?.revision === snapshot.revision) {
            try { localStorage.removeItem(options.key(snapshot.scope)) } catch { /* optional storage */ }
          }
          options.state?.(snapshot, 'saved')
        }
        return true
      } catch (error) {
        if (pending.get(snapshot.scope) === item) {
          options.state?.(snapshot, 'failed', error)
          if (!disposed && (options.retry?.(error) ?? true)) item.retry = setTimeout(() => { void send(item) }, 5000)
        }
        return false
      }
    })()
    flights.add(operation)
    void operation.finally(() => flights.delete(operation))
    return operation
  }
  function stage(scope: string, revision: number, value: T) {
    if (disposed) return
    const snapshot: DraftSnapshot<T> = JSON.parse(JSON.stringify({ scope, revision, value }))
    const previous = pending.get(scope)
    clearTimeout(previous?.timer); clearTimeout(previous?.retry)
    const item: Pending<T> = { snapshot }
    latest.set(scope, snapshot); pending.set(scope, item)
    try { localStorage.setItem(options.key(scope), JSON.stringify(snapshot)) } catch { /* still sync to server */ }
    options.state?.(snapshot, 'saving')
    item.timer = setTimeout(() => { void send(item) }, 350)
  }
  async function flush(scope?: string, keepalive = false) {
    const items = [...pending.values()].filter(item => scope === undefined || item.snapshot.scope === scope)
    const started = items.map(item => send(item, keepalive))
    return (await Promise.all([...flights, ...started])).every(Boolean)
  }
  const leave = () => { void flush(undefined, true) }
  const online = () => { void flush() }
  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', leave)
    window.addEventListener('online', online)
  }
  function dispose() {
    disposed = true
    for (const item of pending.values()) { clearTimeout(item.timer); clearTimeout(item.retry) }
    void flush(undefined, true)
    if (typeof window !== 'undefined') {
      window.removeEventListener('pagehide', leave)
      window.removeEventListener('online', online)
    }
  }
  return { read, stage, forget, flush, dispose }
}
