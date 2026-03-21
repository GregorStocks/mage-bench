// Shared pure-function helpers used by the game viewer, golden viewer,
// and prerender-timeline.  Deduplicated here so changes stay in sync.

import type { Decision, LlmEvent } from '../types/game-export';

export function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function decodeHtmlEntitiesOnce(str: string): string {
  return str.replace(/&(amp|lt|gt|quot|#39|#x27|#\d+);/g, (match, entity: string) => {
    switch (entity) {
      case 'amp':
        return '&';
      case 'lt':
        return '<';
      case 'gt':
        return '>';
      case 'quot':
        return '"';
      case '#39':
      case '#x27':
        return "'";
      default:
        return String.fromCodePoint(parseInt(entity.slice(1), 10));
    }
  });
}

export function formatToolArgs(args: Record<string, unknown> | undefined | null): string {
  if (!args || typeof args !== 'object') return '';
  const keys = Object.keys(args);
  if (keys.length === 0) return '';
  const parts: string[] = [];
  for (const k of keys) {
    let v: unknown = args[k];
    if (typeof v === 'string' && v.length > 40) v = v.substring(0, 40) + '...';
    else if (typeof v === 'object') {
      const s = JSON.stringify(v);
      v = s.length > 40 ? s.substring(0, 40) + '...' : s;
    }
    parts.push(k + '=' + v);
  }
  return parts.join(', ');
}

export function tryFormatJson(str: string | undefined | null): string {
  if (!str || typeof str !== 'string') return str || '';
  try {
    return JSON.stringify(JSON.parse(str), null, 2);
  } catch {
    return str;
  }
}

export function extractSystemMessages(toolCallEvent: { result?: string }): string[] {
  if (!toolCallEvent.result) return [];
  try {
    const r = JSON.parse(toolCallEvent.result) as Record<string, unknown>;
    const chat = (r.recent_chat || []) as unknown[];
    const msgs: string[] = [];
    for (const msg of chat) {
      if (typeof msg === 'string' && msg.indexOf('[System]') !== -1) {
        msgs.push(msg.replace('[System] ', ''));
      }
    }
    return msgs;
  } catch {
    return [];
  }
}

export interface MergedLlmEvent {
  type: string;
  ts: string;
  gameSeq: number;
  player: string;
  reasoning?: string | null;
  thinking?: string | null;
  toolCalls?: unknown;
  costUsd?: number;
  toolResults?: LlmEvent[];
  // Metadata fields preserved from non-merged events (system_message,
  // context_reset, llm_error, stall, auto_pilot_mode, etc.)
  turnsWithoutProgress?: number;
  errorType?: string;
  errorMessage?: string;
  reason?: string;
  message?: string;
  [k: string]: unknown;
}

/** Merge consecutive llm_response + tool_call events into single blocks. */
export function mergeLlmEvents(events: LlmEvent[]): MergedLlmEvent[] {
  const merged: MergedLlmEvent[] = [];
  let i = 0;
  while (i < events.length) {
    const e = events[i];
    if (e.type === 'llm_response') {
      const toolResults: LlmEvent[] = [];
      let j = i + 1;
      while (j < events.length && events[j].type === 'tool_call' && events[j].player === e.player) {
        toolResults.push(events[j]);
        j++;
      }
      const mergedSeq = e.gameSeq || (toolResults.length > 0 ? toolResults[0].gameSeq : 0) || 0;
      merged.push({
        type: 'llm_merged',
        ts: e.ts || '',
        gameSeq: mergedSeq,
        player: e.player,
        reasoning: e.reasoning,
        thinking: e.thinking,
        toolCalls: e.toolCalls,
        costUsd: e.costUsd,
        toolResults,
      });
      i = j;
    } else if (e.type === 'tool_call') {
      merged.push({
        type: 'llm_merged',
        ts: e.ts || '',
        gameSeq: e.gameSeq || 0,
        player: e.player,
        toolResults: [e],
      });
      i++;
    } else {
      // Pass metadata events (system_message, context_reset, etc.) through
      // unchanged so downstream renderers keep all fields.
      merged.push(e as unknown as MergedLlmEvent);
      i++;
    }
  }
  return merged;
}

/**
 * Convert a canonical decision into human-readable text describing what
 * the player chose.
 */
export function chosenDisplayText(decision: Decision): string {
  const chosen = decision.chosen as unknown;
  const chosenArgs = (decision.chosenArgs || {}) as Record<string, unknown>;
  const choices = (decision.choices || []) as Array<Record<string, unknown>>;
  const message = (decision.message || '') as string;
  const pilotCtx = (decision.pilotContext || {}) as Record<string, unknown>;

  const choiceById: Record<string, Record<string, unknown>> = {};
  for (const c of choices) {
    if (c && typeof c === 'object' && c.id) {
      choiceById[c.id as string] = c;
    }
  }
  const incoming = (pilotCtx.incomingAttackers || []) as Array<Record<string, unknown>>;
  for (const a of incoming) {
    if (a && a.id && !choiceById[a.id as string]) {
      choiceById[a.id as string] = a;
    }
  }

  function nameOf(id: string): string {
    const c = choiceById[id];
    return c ? ((c.name || c.description || id) as string) : id;
  }

  function nameWithStats(id: string): string {
    const c = choiceById[id];
    if (!c) return id;
    let n = (c.name || c.description || id) as string;
    if (c.power != null && c.toughness != null) {
      n += ' ' + c.power + '/' + c.toughness;
    }
    return n;
  }

  // Batch attacks
  if (chosenArgs.attackers) {
    let attackers = chosenArgs.attackers;
    if (typeof attackers === 'string') {
      attackers = attackers.split(',').map((s: string) => s.trim());
    }
    const atkArr = attackers as Array<unknown>;
    if (atkArr.length === 1 && atkArr[0] === 'all') {
      const allNames = choices
        .filter((c) => c && typeof c === 'object' && c.id !== 'all')
        .map((c) => nameWithStats(c.id as string));
      return allNames.length > 0
        ? 'Attack with all (' + allNames.join(', ') + ')'
        : 'Attack with all creatures';
    }
    const atkNames = atkArr.map((a) => {
      const id = (typeof a === 'object' && a && (a as Record<string, unknown>).id)
        ? (a as Record<string, unknown>).id as string
        : String(a);
      return nameWithStats(id);
    });
    return 'Attack with ' + atkNames.join(', ');
  }

  // Batch blocks
  if (chosenArgs.blockers) {
    let blockers: unknown = chosenArgs.blockers;
    if (typeof blockers === 'string') {
      try { blockers = JSON.parse(blockers) as unknown; } catch {
        blockers = (blockers as string).split(',').map((s: string) => s.trim());
      }
    }
    const blockArr = blockers as Array<unknown>;
    if (!blockArr || blockArr.length === 0) return 'No blocks';
    const blockParts: string[] = [];
    for (const entry of blockArr) {
      if (typeof entry === 'object' && entry && (entry as Record<string, unknown>).id) {
        const e = entry as Record<string, unknown>;
        blockParts.push(nameOf(e.id as string) + ' blocks ' + nameOf(e.blocks as string));
      } else if (typeof entry === 'string' && entry.indexOf(':') !== -1) {
        const pair = entry.split(':', 2);
        blockParts.push(nameOf(pair[0]) + ' blocks ' + nameOf(pair[1]));
      } else {
        blockParts.push(nameOf(String(entry)));
      }
    }
    return blockParts.join(', ');
  }

  // Boolean response
  if (typeof chosen === 'boolean') {
    const msgLower = message.toLowerCase();
    if (msgLower.indexOf('mulligan') !== -1) {
      return chosen ? 'Mulligan' : 'Keep hand';
    }
    if (!chosen) {
      if (msgLower.indexOf('blocker') !== -1) return 'No blocks';
      return 'Pass';
    }
    return String(chosen);
  }

  // Null chosen with a choice ID
  if (chosen == null && chosenArgs.choice && chosenArgs.choice !== 'no') {
    const resolved = choiceById[chosenArgs.choice as string];
    if (resolved) {
      const rName = (resolved.name || resolved.description || chosenArgs.choice) as string;
      const rAction = resolved.action;
      if (rAction === 'cast') {
        let lbl = 'Cast ' + rName;
        if (resolved.mana_cost) lbl += ' ' + resolved.mana_cost;
        return lbl;
      }
      if (rAction === 'land') return 'Play ' + rName;
      if (rAction === 'activate') return 'Activate ' + rName;
      return rName;
    }
    return chosenArgs.choice as string;
  }

  // Null chosen, empty args = pass
  if (chosen == null) return 'Pass';

  // Index into choices
  if (typeof chosen === 'number' && chosen >= 0 && chosen < choices.length) {
    const c = choices[chosen];
    if (c && typeof c === 'object') {
      const choiceName = (c.name || c.description || String(chosen)) as string;
      const action = c.action;
      if (action === 'cast') {
        let label = 'Cast ' + choiceName;
        if (c.mana_cost) label += ' ' + c.mana_cost;
        return label;
      }
      if (action === 'land') return 'Play ' + choiceName;
      if (action === 'activate') return 'Activate ' + choiceName;
      return choiceName;
    }
    return String(c);
  }

  return String(chosen);
}
