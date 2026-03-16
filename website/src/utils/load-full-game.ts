// Load a single game's complete JSON by ID.
// Unlike loadAllGames() which extracts lightweight metadata for all games,
// this loads the full GameExportV8 for one game (snapshots, actions, etc.).

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

import type { GameExportV8 } from '../types/game-export';

export function loadFullGame(id: string): GameExportV8 {
  const gamesDir = path.join(process.cwd(), 'public', 'games');
  const jsonPath = path.join(gamesDir, id + '.json');
  const gzPath = path.join(gamesDir, id + '.json.gz');

  let raw: string;
  if (fs.existsSync(jsonPath)) {
    raw = fs.readFileSync(jsonPath, 'utf-8');
  } else if (fs.existsSync(gzPath)) {
    raw = zlib.gunzipSync(fs.readFileSync(gzPath)).toString();
  } else {
    throw new Error(`Game file not found: ${id}`);
  }

  return JSON.parse(raw) as GameExportV8;
}
