export type UploadDocumentKind = 'lecture' | 'note' | 'exam' | 'exercise' | 'answer'
export type DocumentKind = UploadDocumentKind | 'generated'
export interface Course { id: string; name: string; version: number }
export interface Health { status: string; llm_configured: boolean; embedding_model: string; embedding_ready: boolean; index_repair_courses?: string[] }
export type ModelApi = 'openai-completions' | 'openai-responses' | 'anthropic-messages' | 'google-generative-ai'
export interface ModelConfiguration {
  provider: string; api: ModelApi; base_url: string; model: string; allow_without_key: boolean
  timeout_seconds: number; context_window: number; max_tokens: number; reasoning: boolean; supports_images: boolean
  has_api_key: boolean; configured: boolean; source: 'none' | 'environment' | 'saved'
}
export interface CatalogModel {
  id: string; name: string; api: ModelApi; base_url: string; context_window: number; max_tokens: number; reasoning: boolean; supports_images: boolean
}
export interface CatalogProvider { id: string; name: string; models: CatalogModel[] }
export interface Locator { label: string; [key: string]: unknown }
export interface Document {
  id: string; filename: string; kind: DocumentKind; year: number | null
  practice_title?: string | null
  status: 'queued' | 'processing' | 'ready' | 'failed'
  warnings: string[]; error?: string | null; units_total: number; units_with_text: number; created_at: string
  chapter_excluded?: boolean
}
export interface Block { id: string; document_id?: string; text: string; locator: Locator; chapter_id: string | null; heading: string | null; order: number }
export interface Chapter { id: string; title: string; position: number; confirmed: boolean; block_ids: string[]; block_count: number; kind?: 'knowledge' | 'support' | 'practice' }
export interface ChapterDraft { id: string | null; title: string; position: number; block_ids: string[] }
export interface Session { id: string; title: string; created_at: string }
export interface Source { chunk_id: string; text: string; filename: string; kind: DocumentKind; year: number | null; chapter_id: string | null; locator: Locator; source_id?: string }
export interface Message { id: string; role: string; content: string; citations: Source[]; created_at: string; practice_context?: PracticeChatContext | null; note_context?: NoteChatContext | null }
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
  resolution: { answer?: string; choices?: string[]; origin?: 'uploaded' | 'embedded' | 'model' | 'manual'; source?: string }
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
