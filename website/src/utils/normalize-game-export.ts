import type { GameExportV9 } from '../types/game-export';

const LEGACY_VERSION = 8;
const CURRENT_VERSION = 9;

const TOP_LEVEL_KEYS: Record<string, string> = {
  gameType: 'game_type',
  deckType: 'deck_type',
  totalTurns: 'total_turns',
  harnessEpoch: 'harness_epoch',
  youtubeUrl: 'youtube_url',
  cardImages: 'card_images',
  cardData: 'card_data',
  llmEvents: 'llm_events',
  gameOver: 'game_over',
  blunderScriptVersion: 'blunder_script_version',
};

const PLAYER_KEYS: Record<string, string> = {
  deckName: 'deck_name',
  deckStrategy: 'deck_strategy',
  reasoningEffort: 'reasoning_effort',
  totalCostUsd: 'total_cost_usd',
  toolCallsOk: 'tool_calls_ok',
  toolCallsFailed: 'tool_calls_failed',
  thinkingTimeSecs: 'thinking_time_secs',
  timedOut: 'timed_out',
};

const LLM_EVENT_BASE_KEYS: Record<string, string> = {
  gameSeq: 'game_seq',
};

const GAME_START_KEYS: Record<string, string> = {
  availableTools: 'available_tools',
};

const LLM_RESPONSE_KEYS: Record<string, string> = {
  toolCalls: 'tool_calls',
  costUsd: 'cost_usd',
};

const TOOL_CALL_KEYS: Record<string, string> = {
  latencyMs: 'latency_ms',
};

const STALL_KEYS: Record<string, string> = {
  turnsWithoutProgress: 'turns_without_progress',
  lastTools: 'last_tools',
};

const CONTEXT_TRIM_KEYS: Record<string, string> = {
  messagesBefore: 'messages_before',
  messagesAfter: 'messages_after',
};

const LLM_ERROR_KEYS: Record<string, string> = {
  errorType: 'error_type',
  errorMessage: 'error_message',
};

const LLM_USAGE_KEYS: Record<string, string> = {
  promptTokens: 'prompt_tokens',
  completionTokens: 'completion_tokens',
  cachedTokens: 'cached_tokens',
  reasoningTokens: 'reasoning_tokens',
};

const ANNOTATION_KEYS: Record<string, string> = {
  decisionIndex: 'decision_index',
  snapshotIndex: 'snapshot_index',
  actionTaken: 'action_taken',
  betterLine: 'better_line',
  llmReasoning: 'llm_reasoning',
};

const DECISION_KEYS: Record<string, string> = {
  snapshotIndex: 'snapshot_index',
  actionType: 'action_type',
  responseType: 'response_type',
  choiceCount: 'choice_count',
  isForced: 'is_forced',
  llmEventIndices: 'llm_event_indices',
  subsequentActions: 'subsequent_actions',
  pilotContext: 'pilot_context',
  chosenArgs: 'chosen_args',
  actionResult: 'action_result',
  castRolledBack: 'cast_rolled_back',
  totalMin: 'total_min',
  totalMax: 'total_max',
  actionSeq: 'action_seq',
};

const PILOT_CONTEXT_KEYS: Record<string, string> = {
  untappedLands: 'untapped_lands',
  landDropsUsed: 'land_drops_used',
  playableCards: 'playable_cards',
  combatPhase: 'combat_phase',
  alreadyAttacking: 'already_attacking',
  incomingAttackers: 'incoming_attackers',
};

const GAME_ERROR_KEYS: Record<string, string> = {
  decisionIndex: 'decision_index',
};

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function objectValue(value: unknown, context: string): Record<string, unknown> {
  invariant(value != null && typeof value === 'object' && !Array.isArray(value), `${context} must be an object`);
  return value as Record<string, unknown>;
}

function arrayValue(value: unknown, context: string): unknown[] {
  invariant(Array.isArray(value), `${context} must be an array`);
  return value;
}

function renameKeys(obj: Record<string, unknown>, mapping: Record<string, string>): Record<string, unknown> {
  const renamed: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj)) {
    renamed[mapping[key] ?? key] = value;
  }
  return renamed;
}

function normalizePlayer(player: unknown): Record<string, unknown> {
  return renameKeys(objectValue(player, 'player'), PLAYER_KEYS);
}

function normalizeLlmUsage(usage: unknown): Record<string, unknown> {
  return renameKeys(objectValue(usage, 'llm usage'), LLM_USAGE_KEYS);
}

function normalizeLlmEvent(event: unknown): Record<string, unknown> {
  let normalized = renameKeys(objectValue(event, 'llm event'), LLM_EVENT_BASE_KEYS);
  switch (normalized.type) {
    case 'game_start':
      normalized = renameKeys(normalized, GAME_START_KEYS);
      break;
    case 'llm_response':
      normalized = renameKeys(normalized, LLM_RESPONSE_KEYS);
      if (normalized.usage != null) {
        normalized.usage = normalizeLlmUsage(normalized.usage);
      }
      break;
    case 'tool_call':
      normalized = renameKeys(normalized, TOOL_CALL_KEYS);
      break;
    case 'stall':
      normalized = renameKeys(normalized, STALL_KEYS);
      break;
    case 'context_trim':
      normalized = renameKeys(normalized, CONTEXT_TRIM_KEYS);
      break;
    case 'llm_error':
      normalized = renameKeys(normalized, LLM_ERROR_KEYS);
      break;
  }
  return normalized;
}

function normalizeAnnotation(annotation: unknown): Record<string, unknown> {
  return renameKeys(objectValue(annotation, 'annotation'), ANNOTATION_KEYS);
}

function normalizePilotContext(context: unknown): Record<string, unknown> {
  return renameKeys(objectValue(context, 'pilot context'), PILOT_CONTEXT_KEYS);
}

function normalizeDecision(decision: unknown): Record<string, unknown> {
  const normalized = renameKeys(objectValue(decision, 'decision'), DECISION_KEYS);
  if (normalized.pilot_context != null) {
    normalized.pilot_context = normalizePilotContext(normalized.pilot_context);
  }
  return normalized;
}

function normalizeGameError(error: unknown): Record<string, unknown> {
  return renameKeys(objectValue(error, 'game error'), GAME_ERROR_KEYS);
}

export function normalizeGameExport(data: unknown): GameExportV9 {
  const raw = objectValue(data, 'game export');
  const version = raw.version;
  invariant(typeof version === 'number', 'game export version must be a number');
  if (version === CURRENT_VERSION) {
    return raw as unknown as GameExportV9;
  }
  invariant(version === LEGACY_VERSION, `unsupported game export version ${String(version)}`);

  const normalized = renameKeys(raw, TOP_LEVEL_KEYS);
  normalized.version = CURRENT_VERSION;
  normalized.players = arrayValue(normalized.players, 'players').map(normalizePlayer);
  normalized.llm_events = arrayValue(normalized.llm_events, 'llm_events').map(normalizeLlmEvent);
  if (normalized.annotations != null) {
    normalized.annotations = arrayValue(normalized.annotations, 'annotations').map(normalizeAnnotation);
  }
  if (normalized.decisions != null) {
    normalized.decisions = arrayValue(normalized.decisions, 'decisions').map(normalizeDecision);
  }
  if (normalized.errors != null) {
    normalized.errors = arrayValue(normalized.errors, 'errors').map(normalizeGameError);
  }
  return normalized as unknown as GameExportV9;
}
