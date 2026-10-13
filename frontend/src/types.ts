export type UploadDocumentKind = 'lecture' | 'note' | 'exam' | 'exercise' | 'answer'
export type DocumentKind = UploadDocumentKind | 'generated'
export interface Course { id: string; name: string; version: number }
export interface Health { status: string; llm_configured: boolean; embedding_model: string; embedding_ready: boolean; index_repair_courses?: string[] }
export type ModelApi = 'openai-completions' | 'openai-responses' | 'openai-codex-responses' | 'anthropic-messages' | 'google-generative-ai' | 'mistral-conversations'
export type ThinkingLevel = 'default' | 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
export interface ModelConfiguration {
  provider: string; api: ModelApi; base_url: string; model: string; allow_without_key: boolean
  timeout_seconds: number; context_window: number; max_tokens: number; reasoning: boolean; supports_images: boolean
  has_api_key: boolean; configured: boolean; source: 'none' | 'environment' | 'saved'
  provider_id?: string; provider_name?: string; model_name?: string; credential_source?: string
}
export interface CatalogModel {
  id: string; name: string; api: ModelApi; base_url: string; context_window: number; max_tokens: number; reasoning: boolean; supports_images: boolean
  thinking_levels?: Exclude<ThinkingLevel, 'default'>[]; compat?: Record<string, boolean | string>; cost?: Record<string, number>
}
export interface CatalogProvider {
  id: string; name: string; base_url: string; env_key: string | null; models: CatalogModel[]
  supports_oauth: boolean; subscription: boolean; oauth_name: string; oauth_label: string
}
export interface ManagedModel {
  id: string; name: string; api: ModelApi; context_window: number; max_tokens: number
  reasoning: boolean; supports_images: boolean; thinking_level: ThinkingLevel; temperature: number | null
  compat: Record<string, boolean | string>
}
export interface ManagedProvider {
  id: string; name: string; provider: string; base_url: string; timeout_seconds: number
  credential_mode: 'api_key' | 'environment' | 'none' | 'oauth'; has_api_key: boolean; has_headers: boolean
  supports_oauth: boolean; oauth_logged_in: boolean
  env_key: string | null; credential_source: string; configured: boolean; models: ManagedModel[]
}
export interface ModelSelection { provider_id: string; model_id: string }
export interface ModelRegistry {
  version: number; providers: ManagedProvider[]; active: ModelSelection | null
  current: ModelConfiguration; source: ModelConfiguration['source']
}
export interface ModelOAuthSession {
  id: string; provider_id: string; provider_name: string
  status: 'pending' | 'waiting_input' | 'connected' | 'failed' | 'cancelled'; message: string
  events: { type: 'auth_url' | 'device_code' | 'progress' | 'info'; url?: string; instructions?: string
    userCode?: string; verificationUri?: string; message?: string; links?: { url: string; label?: string }[] }[]
  prompt: { id: string; type: 'text' | 'secret' | 'select' | 'manual_code'; message: string; placeholder?: string
    options?: { id: string; label: string; description?: string }[] } | null
}
export interface Locator { label: string; [key: string]: unknown }
export interface Document {
  id: string; filename: string; kind: DocumentKind; year: number | null
  practice_title?: string | null
  status: 'queued' | 'processing' | 'ready' | 'failed'
  warnings: string[]; error?: string | null; units_total: number; units_with_text: number; created_at: string
  chapter_excluded?: boolean
}
export interface Block { id: string; document_id?: string; text: string; locator: Locator; chapter_id: string | null; heading: string | null; order: number }
export interface BlockReference { id: string; document_id: string; chapter_id: string | null }
export interface Chapter { id: string; title: string; position: number; confirmed: boolean; block_ids: string[]; block_count: number; kind?: 'knowledge' | 'support' | 'practice' }
export interface ChapterDraft { id: string | null; title: string; position: number; block_ids: string[] }
export interface Session { id: string; title: string; created_at: string }
export interface Source { chunk_id: string; text: string; filename: string; kind: DocumentKind; year: number | null; chapter_id: string | null; locator: Locator; source_id?: string }
export interface WebReference { title: string; url: string; content_kind: 'page_excerpt' | 'search_snippet'; retrieved_at: string }
export interface WebSource extends WebReference {
  chunk_id: string; source_id: string; text: string; filename: string; kind: 'web'; locator: Locator
  external: true; year: null; chapter_id: null
}
export interface TokenUsageTotals {
  input_tokens: number | null; output_tokens: number | null; total_tokens: number | null
  cache_read_tokens: number | null; cache_write_tokens: number | null; reasoning_tokens: number | null
  status: 'none' | 'complete' | 'partial' | 'unavailable'
  model_calls: number; reported_calls: number; unreported_calls: number
}
export interface TokenUsage extends TokenUsageTotals {
  stages: (TokenUsageTotals & { stage: string })[]; note?: string
}
export interface Message { id: string; role: string; content: string; citations: Source[]; created_at: string; practice_context?: PracticeChatContext | null; note_context?: NoteChatContext | null; token_usage?: TokenUsage | null }
export interface Outline { id: string; markdown?: string; status: string; stale: boolean; style?: string; current_style?: string; coverage?: Record<string, unknown>; job_id: string; created_at: string }
export interface Job { id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'; progress: number; stage: string; error?: string | null; outline_id: string }
export const uploadKindLabels: Record<UploadDocumentKind, string> = { lecture: '课件', note: '笔记', exam: '往年真题', exercise: '练习题', answer: '答案' }
export const kindLabels: Record<DocumentKind, string> = { ...uploadKindLabels, generated: 'AI 练习' }

export interface PracticeTranslation {
  stem: string; options: { key: string; text: string }[]
}
export interface PracticeQuestion {
  id: string; number: string; section: string; question_type: 'single_choice' | 'multiple_choice' | 'other'
  stem: string; options: { key: string; text: string }[]; locator: Locator
  translation: PracticeTranslation | null
  wrong_question_id: string | null
}
export interface PracticeState {
  generated?: boolean
  status: 'new' | 'queued' | 'processing' | 'ready' | 'failed'; progress: number
  error: string | null; questions: PracticeQuestion[]
  answer_preparation?: { status: 'new' | 'waiting' | 'processing' | 'ready' | 'failed'; progress: number }
  answer_pairing?: PracticeAnswerPairing
}
export interface PracticeAnswerPairing {
  mode: 'auto' | 'manual' | 'none'; answer_document_id: string | null
  needs_confirmation: boolean; missing: boolean
  embedded: boolean; matched_document_id: string | null; matched_filename: string | null
}
export interface PracticeAnswer {
  answer: string; choices: string[]; origin: 'uploaded' | 'embedded' | 'model'; source: string; sources: Source[]
  correct: boolean | null; selected: string[] | null
  reference_match: boolean | null; grading_basis: 'uploaded' | 'embedded' | 'model'
  wrong_question_id: string | null
  explanation_pending?: boolean
  resolved?: boolean
  external_knowledge?: boolean; model_knowledge?: boolean; web_sources?: WebReference[]
}

export interface PracticeChatContext {
  question_id: string | null; wrong_question_id?: string; filename: string; number: string; stem: string
  original_number?: string
  options: { key: string; text: string }[]; selected: string[]; own_answer: string
}

export interface NoteChatContext {
  outline_id: string; heading: string; selected_text: string; start_line: number; end_line: number
  text?: string; stale?: boolean
  source_text?: string
}

export interface WrongQuestionReview {
  id: string; selected: string[]; own_answer: string; created_at: string
  correct: boolean | null; reference_match: boolean | null; resolved: boolean
  grading_basis: 'uploaded' | 'embedded' | 'model' | null
  resolution: WrongQuestion['resolution']
}

export interface WrongQuestion {
  id: string; course_id: string; source_document_id: string | null; source_question_id: string | null
  question: Omit<PracticeQuestion, 'id' | 'wrong_question_id'> & { filename: string; year: number | null }
  resolution: { answer?: string; choices?: string[]; origin?: 'uploaded' | 'embedded' | 'model' | 'manual'; source?: string; external_knowledge?: boolean; model_knowledge?: boolean; web_sources?: WebReference[] }
  selected: string[]; own_answer: string; reason: 'manual' | 'incorrect' | 'reference'; created_at: string; updated_at: string
  reviews?: WrongQuestionReview[]
}

export interface MemoryPreference {
  id: string; key: string; scope: 'global' | 'course'; content: string; source_quote: string; updated_at: string
}
export interface LearningMemory {
  id: string; topic: string; status: 'needs_review' | 'recent_correct' | 'discussed'
  incorrect_count: number; correct_count: number; question_count: number
  grading_basis: 'uploaded' | 'embedded' | 'model' | 'manual' | null; source: string; updated_at: string
  recent_events: { id: string; kind: string; stem: string; question?: string; number: string; filename: string
    correct: boolean | null; grading_basis: string | null; selected: string[] | null; created_at: string }[]
}
export interface SessionMemory {
  summary: string; topic: string; revision: number; pending: boolean; updated_at: string
}
export interface AgentMemory {
  preferences: MemoryPreference[]; learning: LearningMemory[]; session: SessionMemory | null; updating: boolean
}

export type ReviewSpaceStatus = 'processing' | 'waiting_answer' | 'reading' | 'feedback' | 'paused' | 'failed' | 'completed'
export interface ReviewSpaceStats {
  answered: number; matched: number; independent_matched: number; verified_matched: number; model_graded: number
}
export interface ReviewSpaceQuestion {
  id: string; document_id: string; number: string; stem: string; section: string; filename: string
  question_type: 'single_choice' | 'multiple_choice' | 'other'; options: { key: string; text: string }[]; locator: Locator
  external_knowledge?: boolean; model_knowledge?: boolean; web_sources?: WebReference[]
}
export interface ReviewSpaceStep {
  id: string; kind: 'question' | 'explanation'; topic: string; difficulty?: 'basic' | 'medium' | 'advanced'
  reason: string; repeated?: boolean; assisted?: boolean; question?: ReviewSpaceQuestion; content?: string; sources?: (Source | WebSource)[]
  batch_index?: number; batch_count?: number
  external_knowledge?: boolean; model_knowledge?: boolean; web_sources?: WebReference[]
  revealed?: boolean; revealed_answer?: Pick<PracticeAnswer, 'answer' | 'origin'>
  token_usage?: TokenUsage | null
  search_scope?: { chapter_ids: string[]; chapters: string[] }
  scope_has_gaps?: boolean
}
export interface ReviewSpaceDraft {
  step_id?: string; selected?: string[]; own_answer?: string; used_hint?: boolean; seen_answer?: boolean; draft_revision?: number
}
export interface ReviewSpaceAttempt {
  step_id: string; question_id: string; topic: string; stem: string; difficulty: string
  used_hint: boolean; seen_answer: boolean; repeated: boolean; selected: string[]; correct: boolean | null
  grading_basis: string | null; own_answer?: string; created_at: string
  external_knowledge?: boolean; model_knowledge?: boolean; web_sources?: WebReference[]
}
export interface ReviewSpaceSummary {
  generation_progress?: { completed: number; total: number } | null
  id: string; course_id: string; goal: string; title: string; status: ReviewSpaceStatus
  revision: number; created_at: string; updated_at: string; stats: ReviewSpaceStats
  stage: string; error: string; stop_reason: string
  chapter_ids?: string[]; chapters?: string[]
}
export interface ReviewSpaceMessage {
  id: string; role: 'user' | 'assistant'; kind: 'message' | 'question' | 'explanation' | 'result' | 'summary'
  content: string; created_at: string; step?: ReviewSpaceStep; result?: ReviewSpaceAnswer
  token_usage?: TokenUsage | null
}
export type ReviewSpaceAnswer = Omit<PracticeAnswer, 'grading_basis'> & { grading_basis: PracticeAnswer['grading_basis'] | null; own_answer?: string }
export interface ReviewSpace extends ReviewSpaceSummary {
  failed_token_usage?: TokenUsage | null
  messages: ReviewSpaceMessage[]
  step: ReviewSpaceStep | null; draft: ReviewSpaceDraft
  last_result: (ReviewSpaceAnswer & { step_id: string; question: ReviewSpaceQuestion; topic: string }) | null
  attempts: ReviewSpaceAttempt[]; trace: { action: string; reason: string; topic: string; created_at: string; result: string }[]
  summary: string
}
