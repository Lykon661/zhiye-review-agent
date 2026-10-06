import type { PracticeAnswer } from './types'

// Derive the display from provenance, including older API/cache responses whose
// `correct` field may also have been populated for model reference answers.
export function normalizePracticeAnswer(value: PracticeAnswer): PracticeAnswer {
  const choices = Array.isArray(value.choices) ? value.choices : []
  const selected = Array.isArray(value.selected) ? value.selected : null
  const resolved = value.resolved !== false && (selected === null || choices.length > 0)
  const matches = selected !== null && resolved
    ? [...new Set(selected)].sort().join('|') === [...new Set(choices)].sort().join('|') : null
  const fromSource = value.origin === 'uploaded' || value.origin === 'embedded'
  return { ...value, choices, selected, resolved, correct: fromSource ? matches : null,
    reference_match: fromSource ? null : matches,
    grading_basis: fromSource ? value.origin as 'uploaded' | 'embedded' : 'model' }
}
