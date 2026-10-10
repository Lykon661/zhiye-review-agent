import { nextTick, onUnmounted, ref, type Ref } from 'vue'
import { api, chatStream } from './api'
import type { Message, NoteChatContext, PracticeChatContext, Session, TokenUsage } from './types'

interface VisibleMessage extends Message { pending?: boolean; incomplete?: boolean }
interface Options {
  courseId: Ref<string>
  canChat: () => boolean
  practiceContext: () => PracticeChatContext | null
  noteContext: () => NoteChatContext | null
  namingNewSession: () => boolean
  clearFeedback: () => void
  error: (reason: unknown) => void
  refreshHealth: () => Promise<void>
}
export function useReviewChat(options: Options) {
  const courseId = options.courseId, clearFeedback = options.clearFeedback, setError = options.error
  const sessions = ref<Session[]>([]), sessionId = ref(''), messages = ref<VisibleMessage[]>([])
  const messagesLoading = ref(false), question = ref(''), chatting = ref(false), newSessionBusy = ref(false), chatStatus = ref('')
  const chatBox = ref<HTMLElement | null>(null)
  let sessionGeneration = 0, activeController: AbortController | null = null
  function stopChat() { activeController?.abort() }
  function clearSession() {
    ++sessionGeneration
    messages.value = []; messagesLoading.value = false; sessionId.value = ''; question.value = ''
  }
  function reset() { stopChat(); clearSession(); sessions.value = []; chatStatus.value = '' }
  async function selectSession(id: string) {
    if (chatting.value) return
    sessionId.value = id; const generation = ++sessionGeneration; messagesLoading.value = true; messages.value = []
    try { const result = await api<Message[]>(`/sessions/${id}/messages`); if (generation === sessionGeneration) messages.value = result }
    catch (error) { if (generation === sessionGeneration) setError(error) }
    finally { if (generation === sessionGeneration) { messagesLoading.value = false; await scrollChat() } }
  }
  async function newSession(title = '新复习对话', id = courseId.value) {
    if (newSessionBusy.value) return
    clearFeedback()
    newSessionBusy.value = true
    try {
      const result = await api<Session>(`/courses/${id}/sessions`, { method: 'POST', body: JSON.stringify({ title }) })
      if (id === courseId.value) { ++sessionGeneration; sessions.value.unshift(result); sessionId.value = result.id; messages.value = []; messagesLoading.value = false }
    } catch (error) { if (options.namingNewSession()) throw error; setError(error) }
    finally { newSessionBusy.value = false }
  }
  async function scrollChat() { await nextTick(); if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight }
  async function sendQuestion() {
    const text = question.value.trim()
    if (!text || chatting.value || !options.canChat()) return
    const practice = options.practiceContext(), reading = options.noteContext()
    const context = practice ? JSON.parse(JSON.stringify(practice)) as PracticeChatContext : null
    const note = reading ? { ...reading } : null
    clearFeedback()
    chatting.value = true
    if (!sessionId.value) { await newSession(); if (!sessionId.value) { chatting.value = false; return } }
    const currentSession = sessionId.value
    question.value = ''; chatting.value = true; chatStatus.value = '正在准备检索…'
    const controller = new AbortController(); activeController = controller
    const user: VisibleMessage = { id: `local-user-${Date.now()}`, role: 'user', content: text, citations: [], created_at: new Date().toISOString(), practice_context: context, note_context: note }
    const answer = ref<VisibleMessage>({ id: `local-assistant-${Date.now()}`, role: 'assistant', content: '', citations: [], created_at: new Date().toISOString(), pending: true })
    messages.value.push(user, answer.value)
    // Use the reactive array member so streaming updates render immediately.
    const visibleAnswer = messages.value[messages.value.length - 1]
    let completed = false
    let streamedContent = ''
    let streamTimer: ReturnType<typeof setTimeout> | undefined
    function publishStream() {
      if (streamTimer !== undefined) clearTimeout(streamTimer)
      streamTimer = undefined
      visibleAnswer.content = streamedContent
    }
    try {
      await chatStream(currentSession, text, controller.signal, event => {
        let data: Record<string, unknown>
        try { data = JSON.parse(event.data) } catch { throw new Error('回答流包含无法解析的数据。请重试。') }
        if (event.event === 'status') chatStatus.value = String(data.message || '正在整理答案…')
        if (event.event === 'delta') {
          streamedContent += String(data.text || '')
          if (streamTimer === undefined) streamTimer = setTimeout(() => { publishStream(); void scrollChat() }, 80)
        }
        if ((event.event === 'done' || event.event === 'error') && data.token_usage && typeof data.token_usage === 'object') {
          visibleAnswer.token_usage = data.token_usage as TokenUsage
        }
        if (event.event === 'error') throw new Error(String(data.message || '回答生成失败。'))
        if (event.event === 'done') {
          completed = true; visibleAnswer.id = String(data.message_id || visibleAnswer.id)
          streamedContent = typeof data.content === 'string' ? data.content : streamedContent
          publishStream()
          visibleAnswer.citations = []
        }
      }, context, note)
      if (!completed) throw new Error('回答流意外结束，未收到完成确认。')
      sessions.value = await api<Session[]>(`/courses/${courseId.value}/sessions`)
    } catch (error) {
      visibleAnswer.incomplete = !completed
      if ((error as Error).name === 'AbortError') chatStatus.value = '已停止生成'
      else { setError(error); chatStatus.value = '生成失败'; await options.refreshHealth() }
    } finally {
      publishStream()
      visibleAnswer.pending = false; chatting.value = false; activeController = null
      if (completed) chatStatus.value = ''
      await scrollChat()
    }
  }
  function keyboard(event: KeyboardEvent) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void sendQuestion() } }

  onUnmounted(stopChat)
  return { sessions, sessionId, messages, messagesLoading, question, chatting, newSessionBusy, chatStatus, chatBox,
    clearSession, reset, stopChat, selectSession, newSession, scrollChat, sendQuestion, keyboard }
}
