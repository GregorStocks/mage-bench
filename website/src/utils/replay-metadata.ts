import type { GameExportV7 } from '../types/game-export';

export interface ReplayBlunderCounts {
  questionable: number;
  minor: number;
  moderate: number;
  major: number;
}

export interface ReplayBlunderSummary {
  total: number;
  counts: ReplayBlunderCounts;
}

export function buildReplayTitle(players: GameExportV7['players']): string {
  return players
    .map((player) => {
      const deckName = player.deckName || player.commander || '';
      return deckName ? `${player.name} (${deckName})` : player.name;
    })
    .join(' vs ');
}

export function summarizeReplayBlunders(
  annotations: GameExportV7['annotations'],
): ReplayBlunderSummary | null {
  const counts: ReplayBlunderCounts = {
    questionable: 0,
    minor: 0,
    moderate: 0,
    major: 0,
  };

  let total = 0;
  for (const annotation of annotations) {
    if (annotation.type !== 'blunder') {
      continue;
    }
    counts[annotation.severity] += 1;
    total += 1;
  }

  if (total === 0) {
    return null;
  }

  return { total, counts };
}

export function formatReplayBlunderSummary(summary: ReplayBlunderSummary): string {
  const parts: string[] = [];

  if (summary.counts.major > 0) {
    parts.push(`${summary.counts.major} major`);
  }
  if (summary.counts.moderate > 0) {
    parts.push(`${summary.counts.moderate} moderate`);
  }
  if (summary.counts.minor > 0) {
    parts.push(`${summary.counts.minor} minor`);
  }
  if (summary.counts.questionable > 0) {
    parts.push(`${summary.counts.questionable} questionable`);
  }

  const suffix = summary.total === 1 ? ' blunder' : ' blunders';
  return `${parts.join(', ')}${suffix}`;
}

export function normalizedBlunderScriptVersion(blunderScriptVersion: number | null | undefined): number {
  return blunderScriptVersion || 1;
}
