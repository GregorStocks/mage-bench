// Scan all exported game files and return lightweight metadata entries.
//
// Reading and parsing every game JSON (~375 files, 1 GB) takes 4-5 s.
// This module caches the result on globalThis so the scan runs once per
// Node process — subsequent SSR requests in the Vite dev server reuse it.

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

import type { GameExportV7 } from '../types/game-export';
import type { ReplayBlunderSummary } from './replay-metadata';
import { buildReplayTitle, summarizeReplayBlunders } from './replay-metadata';

export interface GameEntry {
  id: string;
  timestamp: string;
  totalTurns: number;
  winner: string | null;
  players: GameExportV7['players'];
  deckType: string;
  harnessEpoch: number;
  season: number;
  tournament?: string | null;
  youtubeUrl?: string;
  blunderScoreByPlayer?: Record<string, number>;
  blunderScriptVersion?: number | null;
  replayTitle: string;
  replayBlunderSummary: ReplayBlunderSummary | null;
  errors: NonNullable<GameExportV7['errors']>;
}

const CACHE_KEY = Symbol.for('mage-bench:games-metadata');
type BlunderSeverity = GameExportV7['annotations'][number]['severity'];
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

function assertPlayer(player: unknown, file: string, index: number): asserts player is GameExportV7['players'][number] {
  invariant(player != null && typeof player === 'object', `${file}: player ${index} must be an object`);
  const candidate = player as Record<string, unknown>;
  invariant(typeof candidate.name === 'string', `${file}: player ${index} missing name`);
  invariant(typeof candidate.type === 'string', `${file}: player ${index} missing type`);
  invariant(Number.isInteger(candidate.toolCallsOk), `${file}: player ${index} missing toolCallsOk`);
  invariant(Number.isInteger(candidate.toolCallsFailed), `${file}: player ${index} missing toolCallsFailed`);
  invariant(typeof candidate.thinkingTimeSecs === 'number', `${file}: player ${index} missing thinkingTimeSecs`);
}

function assertAnnotation(
  annotation: unknown,
  file: string,
  index: number,
): asserts annotation is GameExportV7['annotations'][number] {
  invariant(annotation != null && typeof annotation === 'object', `${file}: annotation ${index} must be an object`);
  const candidate = annotation as Record<string, unknown>;
  invariant(candidate.type === 'blunder', `${file}: annotation ${index} has invalid type`);
  invariant(typeof candidate.player === 'string', `${file}: annotation ${index} missing player`);
  invariant(Number.isInteger(candidate.snapshotIndex), `${file}: annotation ${index} missing snapshotIndex`);
  invariant(typeof candidate.description === 'string', `${file}: annotation ${index} missing description`);
  invariant(typeof candidate.actionTaken === 'string', `${file}: annotation ${index} missing actionTaken`);
  invariant(typeof candidate.betterLine === 'string', `${file}: annotation ${index} missing betterLine`);
  invariant(
    typeof candidate.severity === 'string' && candidate.severity in BLUNDER_WEIGHTS,
    `${file}: annotation ${index} has invalid severity`,
  );
}

function assertGameExport(data: unknown, file: string): asserts data is GameExportV7 {
  invariant(data != null && typeof data === 'object', `${file}: export must be an object`);
  const candidate = data as Record<string, unknown>;
  invariant(candidate.version === 7, `${file}: expected export version 7`);
  invariant(typeof candidate.id === 'string', `${file}: missing id`);
  invariant(typeof candidate.timestamp === 'string', `${file}: missing timestamp`);
  invariant(typeof candidate.totalTurns === 'number', `${file}: missing totalTurns`);
  invariant(candidate.winner === null || typeof candidate.winner === 'string', `${file}: invalid winner`);
  invariant(typeof candidate.deckType === 'string', `${file}: missing deckType`);
  invariant(Number.isInteger(candidate.harnessEpoch), `${file}: missing harnessEpoch`);
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

  // Build set of .json.gz stems to deduplicate
  const gzStems = new Set<string>();
  const files = fs.readdirSync(gamesDir).filter(f => f.startsWith('game_')).sort();
  for (const f of files) {
    if (f.endsWith('.json.gz')) gzStems.add(f.replace('.json.gz', '.json'));
  }

  for (const file of files) {
    let data: unknown;
    if (file.endsWith('.json') && !file.endsWith('.json.gz')) {
      if (gzStems.has(file)) continue; // prefer .json.gz
      data = JSON.parse(fs.readFileSync(path.join(gamesDir, file), 'utf-8'));
    } else if (file.endsWith('.json.gz')) {
      // Skip if uncompressed version exists (prefer .json)
      const jsonName = file.replace('.json.gz', '.json');
      if (files.includes(jsonName)) {
        data = JSON.parse(fs.readFileSync(path.join(gamesDir, jsonName), 'utf-8'));
      } else {
        const compressed = fs.readFileSync(path.join(gamesDir, file));
        data = JSON.parse(zlib.gunzipSync(compressed).toString());
      }
    } else {
      continue;
    }

    assertGameExport(data, file);
    const players = data.players;

    const entry: GameEntry = {
      id: data.id,
      timestamp: data.timestamp,
      totalTurns: data.totalTurns,
      winner: data.winner,
      players,
      deckType: data.deckType,
      harnessEpoch: data.harnessEpoch,
      season: data.season,
      replayTitle: buildReplayTitle(players),
      replayBlunderSummary: summarizeReplayBlunders(data.annotations),
      errors: data.errors ?? [],
    };
    if (data.youtubeUrl) entry.youtubeUrl = data.youtubeUrl;
    if (data.tournament) entry.tournament = data.tournament;

    // Compute blunder scores from annotations
    const annotations = data.annotations;
    if (annotations != null) {
      entry.blunderScriptVersion = data.blunderScriptVersion ?? null;
      const totalTurns = data.totalTurns;
      if (totalTurns && totalTurns > 0) {
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
          scoreByPlayer[player] = Math.round((weight / totalTurns) * 100) / 100;
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
