// Prerender the game action log at build time.
//
// Computes the full unified timeline (game actions, LLM events, chat,
// annotations, turn/phase separators) and generates HTML for each entry.
// Each entry gets data-first-snap (first visible snapshot index),
// data-kind (for filter checkboxes), and data-seq (for light styling).
//
// At runtime, the viewer just toggles visibility instead of rebuilding DOM.

import type { GameExportV8, LlmEvent, Decision, Annotation } from '../types/game-export';
import {
  chosenDisplayText,
  escapeHtml,
  extractSystemMessages,
  formatToolArgs,
  tryFormatJson,
} from '../scripts/game-log-helpers';

// ── Constants ──

const PLAYER_COLORS = ['player-0', 'player-1', 'player-2', 'player-3'];

const STEP_LABELS: Record<string, string> = {
  PRECOMBAT_MAIN: 'Precombat Main',
  POSTCOMBAT_MAIN: 'Postcombat Main',
  BEGIN_COMBAT: 'Beginning of Combat',
  DECLARE_ATTACKERS: 'Declare Attackers',
  DECLARE_BLOCKERS: 'Declare Blockers',
  COMBAT_DAMAGE: 'Combat Damage',
  FIRST_COMBAT_DAMAGE: 'Combat Damage',
  END_COMBAT: 'End Combat',
  END_TURN: 'End Step',
  CLEANUP: 'Cleanup',
  UPKEEP: 'Upkeep',
  DRAW: 'Draw Step',
  UNTAP: 'Untap',
};

const SPAM_RE = / skip attack$|^Attacker: .+ unblocked$/;

// ── Helpers ──

function decodeHtmlEntities(html: string): string {
  return html
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(parseInt(code, 10)));
}


function formatPhaseStep(phase: string | null | undefined, step: string | null | undefined): string {
  const key = step || phase || '';
  if (STEP_LABELS[key]) return STEP_LABELS[key];
  if (!key) return '';
  return key.toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTurnLabel(playerTurn: number | null, activePlayer: string | null | undefined): string {
  if (!activePlayer && playerTurn == null) return 'Pregame';
  const turnNum = playerTurn != null ? 'Turn ' + playerTurn : 'Turn ?';
  if (activePlayer) return activePlayer + "'s " + turnNum;
  return turnNum;
}

function computePlayerTurnNumbers(snapshots: GameExportV8['snapshots']): (number | null)[] {
  const counts: Record<string, number> = {};
  const result: (number | null)[] = [];
  let lastTurn = -1;
  let lastPlayer: string | null = null;
  for (let i = 0; i < snapshots.length; i++) {
    const snap = snapshots[i];
    const ap = snap.active_player;
    const t = snap.turn;
    if (ap && (t !== lastTurn || ap !== lastPlayer)) {
      counts[ap] = (counts[ap] || 0) + 1;
      lastTurn = t;
      lastPlayer = ap;
    }
    result[i] = ap ? (counts[ap] || 0) : null;
  }
  return result;
}

// ── LLM event processing ──

interface MergedLlmEvent {
  type: string;
  ts: string;
  gameSeq: number;
  /** Max gameSeq across all events in the merged block.  Used for
   *  firstSnap computation so tool results don't appear too early. */
  maxGameSeq: number;
  player: string;
  reasoning?: string | null;
  thinking?: string | null;
  toolCalls?: unknown;
  costUsd?: number;
  toolResults?: LlmEvent[];
  // Metadata events
  turnsWithoutProgress?: number;
  errorType?: string;
  errorMessage?: string;
  reason?: string;
  message?: string;
}

function mergeLlmEvents(events: LlmEvent[]): MergedLlmEvent[] {
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
      // Max gameSeq across all events so the block doesn't appear before
      // its tool results would be individually visible.
      let maxSeq = mergedSeq;
      for (const tr of toolResults) {
        if ((tr.gameSeq || 0) > maxSeq) maxSeq = tr.gameSeq || 0;
      }
      merged.push({
        type: 'llm_merged',
        ts: e.ts || '',
        gameSeq: mergedSeq,
        maxGameSeq: maxSeq,
        player: e.player,
        reasoning: e.reasoning,
        thinking: e.thinking,
        toolCalls: e.toolCalls,
        costUsd: e.costUsd,
        toolResults,
      });
      i = j;
    } else if (e.type === 'tool_call') {
      const seq = e.gameSeq || 0;
      merged.push({
        type: 'llm_merged',
        ts: e.ts || '',
        gameSeq: seq,
        maxGameSeq: seq,
        player: e.player,
        toolResults: [e],
      });
      i++;
    } else {
      const seq = e.gameSeq || 0;
      merged.push({
        type: e.type,
        ts: e.ts || '',
        gameSeq: seq,
        maxGameSeq: seq,
        player: e.player,
        turnsWithoutProgress: e.turnsWithoutProgress,
        errorType: e.errorType,
        errorMessage: e.errorMessage,
        reason: e.reason,
        message: (e as unknown as Record<string, unknown>).message as string | undefined,
      });
      i++;
    }
  }
  return merged;
}

// ── HTML rendering ──

function colorizePlayerNames(message: string, playerColorMap: Record<string, number>): string {
  let escaped = escapeHtml(message);
  const names = Object.keys(playerColorMap);
  names.sort((a, b) => b.length - a.length);
  for (const name of names) {
    const cls = 'action-' + PLAYER_COLORS[playerColorMap[name]];
    const escapedName = escapeHtml(name);
    escaped = escaped.split(escapedName).join('<span class="' + cls + '">' + escapedName + '</span>');
  }
  return escaped;
}

function playerSpan(playerName: string, playerColorMap: Record<string, number>): string {
  const idx = playerColorMap[playerName];
  const cls = idx != null ? 'action-' + PLAYER_COLORS[idx] : '';
  return '<span class="llm-player ' + cls + '">' + escapeHtml(playerName) + '</span>';
}

function playerColorClass(playerName: string, playerColorMap: Record<string, number>): string {
  const idx = playerColorMap[playerName];
  return idx != null ? 'llm-player-' + idx : '';
}

function renderToolResultHtml(
  tc: LlmEvent,
  llmEventIndexToDecision: Record<number, Decision>,
  _playerColorMap: Record<string, number>,
): string | null {
  const origIdx = (tc as unknown as Record<string, unknown>)._origIdx as number | undefined;
  const decision = origIdx != null ? llmEventIndexToDecision[origIdx] || null : null;

  // choose_action mapped to a decision
  if (decision && tc.tool === 'choose_action') {
    const displayText = chosenDisplayText(decision);
    const rawArgs = formatToolArgs(tc.args);
    let html = '<span class="llm-decision-display">';
    html += '<span class="log-badge badge-mcp">mcp</span>';
    html += '<span class="decision-action-text">' + escapeHtml(displayText) + '</span>';
    html += '<details class="llm-tool-raw"><summary>raw</summary><div>';
    html += '<div class="llm-tool-raw-call">' + escapeHtml(tc.tool) + '(' + escapeHtml(rawArgs) + ')</div>';
    if (tc.result) {
      html += '<pre>' + escapeHtml(tryFormatJson(tc.result)) + '</pre>';
    }
    html += '</div></details></span>';
    return html;
  }

  // get_action_choices mapped to a decision: hide
  if (decision && tc.tool === 'get_action_choices') {
    return null;
  }

  // send_chat_message
  if (tc.tool === 'send_chat_message') {
    const rawArgs = formatToolArgs(tc.args);
    let html = '<span class="llm-decision-display">';
    html += '<span class="log-badge badge-mcp">mcp</span>';
    html += '<span class="decision-action-text">send_chat_message</span>';
    html += '<details class="llm-tool-raw"><summary>raw</summary><div>';
    html += '<div class="llm-tool-raw-call">' + escapeHtml(tc.tool!) + '(' + escapeHtml(rawArgs) + ')</div>';
    if (tc.result) {
      html += '<pre>' + escapeHtml(tryFormatJson(tc.result)) + '</pre>';
    }
    html += '</div></details></span>';
    return html;
  }

  // Default tool call
  const argsSummary = formatToolArgs(tc.args);
  let html = '<span class="llm-tool-default">';
  html += '<span class="log-badge badge-llm">llm</span>';
  html += '<details class="llm-tool-detail">';
  html += '<summary>' + escapeHtml(tc.tool || '') + '(' + escapeHtml(argsSummary) + ')</summary>';
  if (tc.result) {
    html += '<pre>' + escapeHtml(tryFormatJson(tc.result)) + '</pre>';
  }
  html += '</details></span>';
  return html;
}

function renderLlmEventHtml(
  event: MergedLlmEvent,
  llmEventIndexToDecision: Record<number, Decision>,
  playerColorMap: Record<string, number>,
): string | null {
  const type = event.type;

  if (type === 'llm_merged') {
    const hasReasoning = event.reasoning && event.reasoning.trim();
    const hasThinking = event.thinking && event.thinking.trim();
    const hasToolResults = event.toolResults && event.toolResults.length > 0;

    if (hasReasoning || hasThinking) {
      let html = '<div>';
      html += '<span class="thinking-badge">thinking</span>';
      html += playerSpan(event.player, playerColorMap);
      html += '</div>';

      if (hasThinking) {
        html += '<details class="llm-thinking-block">';
        html += '<summary>Thinking (' + event.thinking!.length + ' chars)</summary>';
        html += '<pre class="llm-thinking-text">' + escapeHtml(event.thinking!) + '</pre>';
        html += '</details>';
      }

      if (hasReasoning) {
        html += '<div class="llm-reasoning">' + escapeHtml(event.reasoning!) + '</div>';
      }

      if (hasToolResults) {
        for (const tc of event.toolResults!) {
          const tcHtml = renderToolResultHtml(tc, llmEventIndexToDecision, playerColorMap);
          if (tcHtml) html += tcHtml;
        }
      }

      // Wrap in outer div with classes
      const cls = 'llm-event llm-thought ' + playerColorClass(event.player, playerColorMap);
      return '<div class="' + cls + '">' + html + '</div>';
    } else if (hasToolResults) {
      let innerHtml = playerSpan(event.player, playerColorMap);
      let hasVisible = false;
      for (const tc of event.toolResults!) {
        const tcHtml = renderToolResultHtml(tc, llmEventIndexToDecision, playerColorMap);
        if (tcHtml) {
          innerHtml += tcHtml;
          hasVisible = true;
        }
      }
      if (!hasVisible) return null;
      return '<div class="llm-event llm-compact">' + innerHtml + '</div>';
    }
    return null;
  }

  if (type === 'context_trim') return null;

  if (type === 'system_message') {
    return '<div class="llm-event llm-system-message">'
      + '<span class="log-badge badge-llm">llm</span>'
      + playerSpan(event.player, playerColorMap)
      + ' <span class="system-message-text">' + escapeHtml(event.message || '') + '</span>'
      + '</div>';
  }

  // Metadata events
  let metaText: string;
  if (type === 'stall') {
    metaText = event.player + ' stalled (' + (event.turnsWithoutProgress || 0) + ' turns without progress)';
  } else if (type === 'llm_error') {
    metaText = event.player + ' error: ' + (event.errorType || '') + ' ' + (event.errorMessage || '');
  } else if (type === 'context_reset') {
    metaText = event.player + ' context reset: ' + (event.reason || '');
  } else if (type === 'auto_pilot_mode') {
    metaText = event.player + ' switched to auto-pilot: ' + (event.reason || '');
  } else {
    metaText = event.player + ' ' + type;
  }

  return '<div class="llm-event llm-meta">'
    + '<span class="log-badge badge-llm">llm</span>'
    + escapeHtml(metaText)
    + '</div>';
}

function renderAnnotationHtml(ann: Annotation): string {
  let html = '<div class="annotation-header">';
  html += '<span class="annotation-badge severity-' + ann.severity + '">';
  html += ann.severity === 'questionable' ? 'questionable' : ann.severity + ' blunder';
  html += '</span></div>';

  const descText = ann.description || '';
  const COLLAPSE_THRESHOLD = 120;
  if (descText.length <= COLLAPSE_THRESHOLD) {
    html += '<div class="annotation-description">' + escapeHtml(descText) + '</div>';
  } else {
    const firstSentEnd = descText.indexOf('. ');
    const summaryText = firstSentEnd > 0 && firstSentEnd < COLLAPSE_THRESHOLD
      ? descText.slice(0, firstSentEnd + 1)
      : descText.slice(0, COLLAPSE_THRESHOLD) + '\u2026';
    html += '<details class="annotation-description-details">';
    html += '<summary class="annotation-description">' + escapeHtml(summaryText) + '</summary>';
    html += '<div class="annotation-description">' + escapeHtml(descText) + '</div>';
    html += '</details>';
  }

  html += '<details class="annotation-details"><summary>Analysis</summary><div>';
  html += '<div class="annotation-field"><strong>Action taken:</strong> ' + escapeHtml(ann.actionTaken) + '</div>';
  html += '<div class="annotation-field"><strong>Better line:</strong> ' + escapeHtml(ann.betterLine) + '</div>';
  if (ann.llmReasoning) {
    html += '<div class="annotation-field"><strong>Why the LLM erred:</strong> ' + escapeHtml(ann.llmReasoning) + '</div>';
  }
  html += '</div></details>';

  return '<div class="annotation-block severity-' + ann.severity + '">' + html + '</div>';
}

// ── First-snap binary search ──

/** Find first snapshot index where snapshotSeqs[i] >= targetSeq.
 *  Returns snapshotSeqs.length (past end) if no snapshot qualifies,
 *  so the entry stays permanently hidden — matching the legacy behavior
 *  where actions with seq > every snapshot's seq were never shown. */
function findFirstSnapForAction(targetSeq: number, snapshotSeqs: number[]): number {
  let lo = 0;
  let hi = snapshotSeqs.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (snapshotSeqs[mid] < targetSeq) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Find first snapshot index where the LLM event is visible.
 *  Event is visible at i if snapshots[i+1].seq > gameSeq or i is last. */
function findFirstSnapForLlm(gameSeq: number, snapshotSeqs: number[]): number {
  // Find first index where seq > gameSeq
  let lo = 0;
  let hi = snapshotSeqs.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (snapshotSeqs[mid] <= gameSeq) lo = mid + 1;
    else hi = mid;
  }
  // lo is first index where seq > gameSeq
  // Event appears at snapshot lo-1 (since at lo-1, next snap seq > gameSeq)
  return lo > 0 ? lo - 1 : 0;
}

// ── Timeline entry ──

interface TimelineEntry {
  html: string;
  firstSnap: number;
  kind: string;
  sortSeq: number;
  sortPriority: number; // -2 turn-sep, -1 phase-sep, 0 normal, 1 annotation
}

// ── Main export ──

export interface PrerenderResult {
  /** HTML strings for all timeline entries, in display order */
  entries: string[];
  /** Total number of snapshots */
  snapshotCount: number;
  /** Whether the game has LLM events */
  hasLlm: boolean;
  /** Whether the game has chat messages */
  hasChat: boolean;
  /** Whether the game has blunder annotations */
  hasAnnotations: boolean;
  /** Precomputed running LLM costs per snapshot index, keyed by player name.
   *  Allows stripping llmEvents from the inline JSON. */
  runningCostBySnapshot: Record<string, number>[];
}

export function prerenderTimeline(game: GameExportV8): PrerenderResult {
  const snapshots = game.snapshots;
  const snapshotSeqs = snapshots.map((s) => s.seq);
  const snapshotCount = snapshots.length;
  const lastSnapIdx = snapshotCount - 1;

  // Player color map
  const playerColorMap: Record<string, number> = {};
  for (let i = 0; i < (game.players || []).length; i++) {
    playerColorMap[game.players[i].name] = i % 4;
  }

  // Fill in missing gameSeq on llmEvents
  const llmEvents = game.llmEvents || [];
  let lastSeq = 0;
  for (const e of llmEvents) {
    if (e.gameSeq != null) {
      lastSeq = e.gameSeq;
    } else {
      (e as Record<string, unknown>).gameSeq = lastSeq;
    }
  }

  // Stamp _origIdx for decision lookup
  for (let i = 0; i < llmEvents.length; i++) {
    (llmEvents[i] as unknown as Record<string, unknown>)._origIdx = i;
  }

  // Build llmEvent index → decision reverse lookup
  const decisions = game.decisions || [];
  const llmEventIndexToDecision: Record<number, Decision> = {};
  for (const d of decisions) {
    for (const ei of (d.llmEventIndices || [])) {
      llmEventIndexToDecision[ei] = d;
    }
  }

  // Per-player turn numbers
  const playerTurnNumbers = computePlayerTurnNumbers(snapshots);

  // Phase transitions
  interface PhaseTransition {
    index: number;
    ts: string;
    seq: number;
    turn: number;
    playerTurn: number | null;
    phase: string | null;
    step: string | null;
    turnChanged: boolean;
    active_player: string | null;
  }
  const phaseTransitions: PhaseTransition[] = [];
  for (let i = 1; i < snapshots.length; i++) {
    const snap = snapshots[i];
    const prev = snapshots[i - 1];
    const turnChanged = snap.turn !== prev.turn;
    const phaseChanged = snap.phase !== prev.phase || snap.step !== prev.step;
    if (turnChanged || phaseChanged) {
      phaseTransitions.push({
        index: i,
        ts: snap.ts || '',
        seq: snap.seq || 0,
        turn: snap.turn,
        playerTurn: playerTurnNumbers[i],
        phase: snap.phase,
        step: snap.step,
        turnChanged,
        active_player: snap.active_player,
      });
    }
  }

  // Annotation → decision snapshot index
  const annotationDecisionSnap: number[] = [];
  if (game.annotations) {
    for (const ann of game.annotations) {
      const decision = decisions[ann.decisionIndex];
      annotationDecisionSnap.push(decision.snapshotIndex);
    }
  }

  // Extract system messages from all llmEvents and backdate them
  const systemMessages: LlmEvent[] = [];
  const lastPlayerTs: Record<string, string> = {};
  const lastPlayerSeq: Record<string, number> = {};
  for (const e of llmEvents) {
    if (e.type === 'tool_call') {
      const sysmsgs = extractSystemMessages(e);
      if (sysmsgs.length > 0) {
        const backdatedTs = lastPlayerTs[e.player] || e.ts || '';
        const backdatedSeq = lastPlayerSeq[e.player] || e.gameSeq || 0;
        for (const msg of sysmsgs) {
          systemMessages.push({
            type: 'system_message',
            ts: backdatedTs,
            gameSeq: backdatedSeq,
            player: e.player,
            message: msg,
          } as unknown as LlmEvent);
        }
      }
      lastPlayerTs[e.player] = e.ts || '';
      lastPlayerSeq[e.player] = e.gameSeq || 0;
    }
  }

  // Merge all LLM events (including system messages)
  const allLlmEvents = [...llmEvents, ...systemMessages];
  const mergedLlm = mergeLlmEvents(allLlmEvents);

  // Extract chat from LLM events
  interface ChatEntry {
    ts: string;
    from: string;
    message: string;
    gameSeq: number;
  }
  const chatFromLlm: ChatEntry[] = [];
  for (const e of llmEvents) {
    if (e.type !== 'tool_call' || e.tool !== 'send_chat_message') continue;
    chatFromLlm.push({
      ts: e.ts || '',
      from: e.player,
      message: (e.args && (e.args as Record<string, unknown>).message as string) || '',
      gameSeq: e.gameSeq || 0,
    });
  }

  // Build chat dedup set from game actions
  const chatDedup = new Set<string>();
  for (const a of (game.actions || [])) {
    if (a.type === 'chat') {
      const decoded = decodeHtmlEntities(a.message || '');
      chatDedup.add((a.from || '') + '|' + decoded);
    }
  }

  // Content flags
  const hasLlm = llmEvents.length > 0;
  const hasChat = (game.actions || []).some((a) => a.type === 'chat')
    || llmEvents.some((e) => e.tool === 'send_chat_message');
  const hasAnnotations = (game.annotations || []).length > 0;

  // ── Build timeline entries ──

  const timeline: TimelineEntry[] = [];

  // Game actions (excluding turn_change, phase_change, chat, spam)
  for (const a of (game.actions || [])) {
    if (a.type === 'turn_change' || a.type === 'phase_change') continue;
    if (a.type === 'chat') {
      // Chat from game actions
      const firstSnap = findFirstSnapForAction(a.seq, snapshotSeqs);
      const fromIdx = playerColorMap[a.from || ''];
      const fromCls = fromIdx != null ? 'action-' + PLAYER_COLORS[fromIdx] : '';
      const html = '<div class="chat-line">'
        + '<span class="chat-badge">chat</span>'
        + '<span class="chat-from ' + fromCls + '">' + escapeHtml(a.from || '') + ':</span> '
        + escapeHtml(a.message || '')
        + '</div>';
      timeline.push({ html, firstSnap, kind: 'chat', sortSeq: a.seq, sortPriority: 0 });
      continue;
    }
    if (!a.message) continue;
    if (SPAM_RE.test(a.message)) continue;
    const firstSnap = findFirstSnapForAction(a.seq, snapshotSeqs);
    const html = '<div class="action-line">'
      + '<span class="log-badge badge-game">game</span>'
      + colorizePlayerNames(a.message, playerColorMap)
      + '</div>';
    timeline.push({ html, firstSnap, kind: 'game', sortSeq: a.seq, sortPriority: 0 });
  }

  // Chat from LLM (deduplicated)
  for (const c of chatFromLlm) {
    if (chatDedup.has(c.from + '|' + c.message)) continue;
    const firstSnap = findFirstSnapForLlm(c.gameSeq, snapshotSeqs);
    const fromIdx = playerColorMap[c.from];
    const fromCls = fromIdx != null ? 'action-' + PLAYER_COLORS[fromIdx] : '';
    const html = '<div class="chat-line">'
      + '<span class="chat-badge">chat</span>'
      + '<span class="chat-from ' + fromCls + '">' + escapeHtml(c.from || '') + ':</span> '
      + escapeHtml(c.message || '')
      + '</div>';
    timeline.push({ html, firstSnap, kind: 'chat', sortSeq: c.gameSeq, sortPriority: 0 });
  }

  // Merged LLM events — use maxGameSeq for firstSnap so tool results
  // don't appear before the snapshot where they'd individually be visible.
  for (const m of mergedLlm) {
    const eventHtml = renderLlmEventHtml(m, llmEventIndexToDecision, playerColorMap);
    if (!eventHtml) continue;
    const firstSnap = findFirstSnapForLlm(m.maxGameSeq, snapshotSeqs);
    timeline.push({ html: eventHtml, firstSnap, kind: 'llm', sortSeq: m.gameSeq, sortPriority: 0 });
  }

  // Annotations
  if (game.annotations) {
    for (let annIdx = 0; annIdx < game.annotations.length; annIdx++) {
      const ann = game.annotations[annIdx];
      const firstSnap = annotationDecisionSnap[annIdx];
      const decSnap = snapshots[firstSnap] || {};
      const annHtml = renderAnnotationHtml(ann);
      timeline.push({ html: annHtml, firstSnap, kind: 'annotation', sortSeq: decSnap.seq || 0, sortPriority: 1 });
    }
  }

  // Phase/turn separators
  for (const pt of phaseTransitions) {
    if (pt.turnChanged) {
      const label = formatTurnLabel(pt.playerTurn, pt.active_player);
      const html = '<div class="turn-separator">\u2014 ' + escapeHtml(label) + ' \u2014</div>';
      timeline.push({ html, firstSnap: pt.index, kind: 'turn-sep', sortSeq: pt.seq, sortPriority: -2 });
    } else {
      const label = formatPhaseStep(pt.phase, pt.step);
      const html = '<div class="phase-separator">\u2014 ' + escapeHtml(label) + ' \u2014</div>';
      timeline.push({ html, firstSnap: pt.index, kind: 'phase-sep', sortSeq: pt.seq, sortPriority: -1 });
    }
  }

  // Game result lines (only at last snapshot)
  if (game.winner) {
    const winIdx = playerColorMap[game.winner];
    const winCls = winIdx != null ? 'action-' + PLAYER_COLORS[winIdx] : '';
    const html = '<div class="game-result-line">'
      + '<span class="' + winCls + '">' + escapeHtml(game.winner) + '</span> wins the game!'
      + '</div>';
    timeline.push({ html, firstSnap: lastSnapIdx, kind: 'game', sortSeq: Infinity, sortPriority: 0 });
  }
  for (const p of (game.players || [])) {
    if (!p.timedOut) continue;
    const pIdx = playerColorMap[p.name];
    const pCls = pIdx != null ? 'action-' + PLAYER_COLORS[pIdx] : '';
    const html = '<div class="game-result-line game-result-timeout">'
      + '<span class="' + pCls + '">' + escapeHtml(p.name) + '</span> ran out of time'
      + '</div>';
    timeline.push({ html, firstSnap: lastSnapIdx, kind: 'game', sortSeq: Infinity, sortPriority: 0 });
  }

  // Sort by seq, then kind priority
  timeline.sort((a, b) => {
    if (a.sortSeq !== b.sortSeq) return a.sortSeq - b.sortSeq;
    return a.sortPriority - b.sortPriority;
  });

  // Generate final HTML strings with data attributes
  const entries: string[] = [];
  for (const entry of timeline) {
    const hidden = entry.firstSnap > 0 ? ' hidden' : '';
    // Wrap in a container div with data attributes
    entries.push(
      '<div data-first-snap="' + entry.firstSnap
      + '" data-kind="' + entry.kind
      + '" data-seq="' + (entry.sortSeq === Infinity ? 999999999 : entry.sortSeq)
      + '"' + (hidden ? ' class="hidden"' : '') + '>'
      + entry.html
      + '</div>',
    );
  }

  // Precompute running LLM costs per snapshot so llmEvents can be stripped
  // from the inline JSON. Uses a single-pass pointer approach: O(S + E).
  const costEvents = llmEvents
    .filter((e) => e.costUsd && e.player && e.ts)
    .sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));

  const runningCostBySnapshot: Record<string, number>[] = [];
  let costPtr = 0;
  const currentCosts: Record<string, number> = {};
  for (let i = 0; i < snapshotCount; i++) {
    const nextSnap = i < snapshotCount - 1 ? snapshots[i + 1] : null;
    const cutoffTs = nextSnap ? (nextSnap.ts || '') : '';
    while (costPtr < costEvents.length) {
      const e = costEvents[costPtr];
      if (cutoffTs && (e.ts || '') >= cutoffTs) break;
      currentCosts[e.player] = (currentCosts[e.player] || 0) + (e.costUsd || 0);
      costPtr++;
    }
    runningCostBySnapshot.push({ ...currentCosts });
  }

  return { entries, snapshotCount, hasLlm, hasChat, hasAnnotations, runningCostBySnapshot };
}
