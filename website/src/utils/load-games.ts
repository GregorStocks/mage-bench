// Scan all exported game files and return lightweight metadata entries.
//
// Reading and parsing every game JSON5 (~375 files, 1 GB) takes 4-5 s.
// This module caches the result on globalThis so the scan runs once per
// Node process — subsequent SSR requests in the Vite dev server reuse it.

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

import type { GameExportV9 } from '../types/game-export';
import { normalizeGameExport } from './normalize-game-export';
import { parseJSON5 } from './parse-json5';
import type { ReplayBlunderSummary } from './replay-metadata';
import { buildReplayTitle, summarizeReplayBlunders } from './replay-metadata';

export interface GameEntry {
  id: string;
  timestamp: string;
  total_turns: number;
  winner: string | null;
  players: GameExportV9['players'];
  deck_type: string;
  harness_epoch: number;
  season: number;
  tournament?: string | null;
  youtube_url?: string;
  blunderScoreByPlayer?: Record<string, number>;
  blunder_script_version?: number | null;
  replayTitle: string;
  replayBlunderSummary: ReplayBlunderSummary | null;
  errors: NonNullable<GameExportV9['errors']>;
}

const CACHE_KEY = Symbol.for('mage-bench:games-metadata');
type BlunderSeverity = GameExportV9['annotations'][number]['severity'];
const BLUNDER_WEIGHTS: Record<BlunderSeverity, number> = {
  questionable: 0,
  minor: 1,
  moderate: 2,
  major: 4,
};
type GamesCacheGlobal = typeof globalThis & { [CACHE_KEY]?: GameEntry[] };

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function assertPlayer(player: unknown, file: string, index: number): asserts player is GameExportV9['players'][number] {
  invariant(player != null && typeof player === 'object', `${file}: player ${index} must be an object`);
  const candidate = player as Record<string, unknown>;
  invariant(typeof candidate.name === 'string', `${file}: player ${index} missing name`);
  invariant(typeof candidate.type === 'string', `${file}: player ${index} missing type`);
  invariant(Number.isInteger(candidate.tool_calls_ok), `${file}: player ${index} missing tool_calls_ok`);
  invariant(Number.isInteger(candidate.tool_calls_failed), `${file}: player ${index} missing tool_calls_failed`);
  invariant(typeof candidate.thinking_time_secs === 'number', `${file}: player ${index} missing thinking_time_secs`);
}

function assertAnnotation(
  annotation: unknown,
  file: string,
  index: number,
): asserts annotation is GameExportV9['annotations'][number] {
  invariant(annotation != null && typeof annotation === 'object', `${file}: annotation ${index} must be an object`);
  const candidate = annotation as Record<string, unknown>;
  invariant(candidate.type === 'blunder', `${file}: annotation ${index} has invalid type`);
  invariant(typeof candidate.player === 'string', `${file}: annotation ${index} missing player`);
  invariant(Number.isInteger(candidate.decision_index), `${file}: annotation ${index} missing decision_index`);
  invariant(typeof candidate.description === 'string', `${file}: annotation ${index} missing description`);
  invariant(typeof candidate.action_taken === 'string', `${file}: annotation ${index} missing action_taken`);
  invariant(typeof candidate.better_line === 'string', `${file}: annotation ${index} missing better_line`);
  invariant(
    typeof candidate.severity === 'string' && candidate.severity in BLUNDER_WEIGHTS,
    `${file}: annotation ${index} has invalid severity`,
  );
}

function assertGameExport(data: unknown, file: string): asserts data is GameExportV9 {
  invariant(data != null && typeof data === 'object', `${file}: export must be an object`);
  const candidate = data as Record<string, unknown>;
  invariant(candidate.version === 9, `${file}: expected export version 9`);
  invariant(typeof candidate.id === 'string', `${file}: missing id`);
  invariant(typeof candidate.timestamp === 'string', `${file}: missing timestamp`);
  invariant(typeof candidate.total_turns === 'number', `${file}: missing total_turns`);
  invariant(candidate.winner === null || typeof candidate.winner === 'string', `${file}: invalid winner`);
  invariant(typeof candidate.deck_type === 'string', `${file}: missing deck_type`);
  invariant(Number.isInteger(candidate.harness_epoch), `${file}: missing harness_epoch`);
  invariant(Number.isInteger(candidate.season), `${file}: missing season`);
  invariant('tournament' in candidate, `${file}: missing tournament`);
  invariant(candidate.tournament === null || typeof candidate.tournament === 'string', `${file}: invalid tournament`);
  invariant(Array.isArray(candidate.players), `${file}: players must be an array`);
  candidate.players.forEach((player, index) => assertPlayer(player, file, index));
  invariant(Array.isArray(candidate.annotations), `${file}: annotations must be an array`);
  candidate.annotations.forEach((annotation, index) => assertAnnotation(annotation, file, index));
}

function scanGames(): GameEntry[] {
  const gamesDir = path.join(process.cwd(), 'public', 'games');
  invariant(fs.existsSync(gamesDir), `Missing games directory: ${gamesDir}`);

  const games: GameEntry[] = [];

  // Build set of .json5.gz stems to deduplicate
  const gzStems = new Set<string>();
  const files = fs.readdirSync(gamesDir).filter(f => f.startsWith('game_')).sort();
  for (const f of files) {
    if (f.endsWith('.json5.gz')) gzStems.add(f.replace('.json5.gz', '.json5'));
  }

  for (const file of files) {
    let data: unknown;
    if (file.endsWith('.json5') && !file.endsWith('.json5.gz')) {
      if (gzStems.has(file)) continue; // prefer .json5.gz
      data = normalizeGameExport(parseJSON5(fs.readFileSync(path.join(gamesDir, file), 'utf-8')));
    } else if (file.endsWith('.json5.gz')) {
      const compressed = fs.readFileSync(path.join(gamesDir, file));
      data = normalizeGameExport(parseJSON5(zlib.gunzipSync(compressed).toString()));
    } else {
      continue;
    }

    assertGameExport(data, file);
    const players = data.players;

    const entry: GameEntry = {
      id: data.id,
      timestamp: data.timestamp,
      total_turns: data.total_turns,
      winner: data.winner,
      players,
      deck_type: data.deck_type,
      harness_epoch: data.harness_epoch,
      season: data.season,
      replayTitle: buildReplayTitle(players),
      replayBlunderSummary: summarizeReplayBlunders(data.annotations),
      errors: data.errors ?? [],
    };
    if (data.youtube_url) entry.youtube_url = data.youtube_url;
    if (data.tournament) entry.tournament = data.tournament;

    // Compute blunder scores from annotations
    const annotations = data.annotations;
    if (annotations != null) {
      entry.blunder_script_version = data.blunder_script_version ?? null;
      const total_turns = data.total_turns;
      if (total_turns && total_turns > 0) {
        const weightedByPlayer: Record<string, number> = {};
        for (const p of players) {
          weightedByPlayer[p.name] = 0;
        }
        for (const a of annotations) {
          if (a.type !== 'blunder') continue;
          const player = a.player || 'Unknown';
          weightedByPlayer[player] = (weightedByPlayer[player] || 0) + BLUNDER_WEIGHTS[a.severity];
        }
        const scoreByPlayer: Record<string, number> = {};
        for (const [player, weight] of Object.entries(weightedByPlayer)) {
          scoreByPlayer[player] = Math.round((weight / total_turns) * 100) / 100;
        }
        entry.blunderScoreByPlayer = scoreByPlayer;
      }
    }

    games.push(entry);
  }

  games.sort((a, b) => b.id.localeCompare(a.id));
  return games;
}

/** Return all game metadata entries, cached across SSR requests. */
export function loadAllGames(): GameEntry[] {
  const cacheGlobal = globalThis as GamesCacheGlobal;
  if (cacheGlobal[CACHE_KEY] == null) {
    cacheGlobal[CACHE_KEY] = scanGames();
  }
  return cacheGlobal[CACHE_KEY];
}
