// Scan all exported game files and return lightweight metadata entries.
//
// Reading and parsing every game JSON (~375 files, 1 GB) takes 4-5 s.
// This module caches the result on globalThis so the scan runs once per
// Node process — subsequent SSR requests in the Vite dev server reuse it.

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

export interface GameEntry {
  id: string;
  timestamp: string;
  totalTurns: number;
  winner: string | null;
  players: any[];
  deckType: string;
  harnessEpoch: number | null;
  season: number;
  tournament?: any;
  youtubeUrl?: string;
  blunderScoreByPlayer?: Record<string, number>;
  blunderScriptVersion?: number | null;
}

const CACHE_KEY = Symbol.for('mage-bench:games-metadata');
const BLUNDER_WEIGHTS: Record<string, number> = { minor: 1, moderate: 2, major: 4 };

function scanGames(): GameEntry[] {
  const gamesDir = path.join(process.cwd(), 'public', 'games');
  if (!fs.existsSync(gamesDir)) return [];

  const games: GameEntry[] = [];

  // Build set of .json.gz stems to deduplicate
  const gzStems = new Set<string>();
  const files = fs.readdirSync(gamesDir).filter(f => f.startsWith('game_')).sort();
  for (const f of files) {
    if (f.endsWith('.json.gz')) gzStems.add(f.replace('.json.gz', '.json'));
  }

  for (const file of files) {
    let data;
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

    const players = data.players || [];

    // Backfill tool call counts from llmEvents if not already on players
    if (players.length > 0 && !players.some((p: any) => p.toolCallsOk != null)) {
      const toolOk: Record<string, number> = {};
      const toolFailed: Record<string, number> = {};
      for (const ev of (data.llmEvents || [])) {
        if (ev.type !== 'tool_call' || !ev.player) continue;
        let isFail = false;
        if (ev.result) {
          try {
            const r = JSON.parse(ev.result);
            if (r && typeof r === 'object' && r.success === false) isFail = true;
          } catch {}
        }
        if (isFail) toolFailed[ev.player] = (toolFailed[ev.player] || 0) + 1;
        else toolOk[ev.player] = (toolOk[ev.player] || 0) + 1;
      }
      for (const p of players) {
        if (toolOk[p.name] != null || toolFailed[p.name] != null) {
          p.toolCallsOk = toolOk[p.name] || 0;
          p.toolCallsFailed = toolFailed[p.name] || 0;
        }
      }
    }

    // Backfill thinking time from llmEvents if not already on players
    if (players.length > 0 && !players.some((p: any) => p.thinkingTimeSecs != null)) {
      const llmEvents: any[] = data.llmEvents || [];
      if (llmEvents.length > 1) {
        const thinking: Record<string, number> = {};
        for (let i = 0; i < llmEvents.length - 1; i++) {
          const player = llmEvents[i].player;
          if (!player) continue;
          const tsA = llmEvents[i].ts;
          const tsB = llmEvents[i + 1].ts;
          if (!tsA || !tsB) continue;
          const gap = (new Date(tsB).getTime() - new Date(tsA).getTime()) / 1000;
          if (gap > 0) thinking[player] = (thinking[player] || 0) + gap;
        }
        for (const p of players) {
          if (thinking[p.name] != null) {
            p.thinkingTimeSecs = Math.round(thinking[p.name] * 10) / 10;
          }
        }
      }
    }

    const entry: GameEntry = {
      id: data.id,
      timestamp: data.timestamp,
      totalTurns: data.totalTurns,
      winner: data.winner,
      players: players,
      deckType: data.deckType || '',
      harnessEpoch: data.harnessEpoch ?? null,
      season: data.season ?? 0,
    };
    if (data.youtubeUrl) entry.youtubeUrl = data.youtubeUrl;
    if (data.tournament) entry.tournament = data.tournament;

    // Compute blunder scores from annotations
    const annotations = data.annotations;
    if (annotations != null) {
      entry.blunderScriptVersion = data.blunderScriptVersion ?? null;
      const totalTurns = data.totalTurns as number;
      if (totalTurns && totalTurns > 0) {
        const weightedByPlayer: Record<string, number> = {};
        for (const p of players) {
          weightedByPlayer[p.name] = 0;
        }
        for (const a of annotations) {
          if (a.type !== "blunder") continue;
          const player = a.player || "Unknown";
          weightedByPlayer[player] = (weightedByPlayer[player] || 0) + (BLUNDER_WEIGHTS[a.severity] || 0);
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
  const g = globalThis as any;
  if (!g[CACHE_KEY]) {
    g[CACHE_KEY] = scanGames();
  }
  return g[CACHE_KEY];
}
