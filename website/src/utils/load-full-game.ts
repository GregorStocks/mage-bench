// Load a single game's complete JSON5 by ID.
// Unlike loadAllGames() which extracts lightweight metadata for all games,
// this loads the full GameExportV8 for one game (snapshots, actions, etc.).

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

import type { GameExportV8 } from '../types/game-export';
import { parseJSON5 } from './parse-json5';

export function loadFullGame(id: string): GameExportV8 {
  const gamesDir = path.join(process.cwd(), 'public', 'games');
  const json5Path = path.join(gamesDir, id + '.json5');
  const json5GzPath = path.join(gamesDir, id + '.json5.gz');

  let raw: string;
  if (fs.existsSync(json5Path)) {
    raw = fs.readFileSync(json5Path, 'utf-8');
  } else if (fs.existsSync(json5GzPath)) {
    raw = zlib.gunzipSync(fs.readFileSync(json5GzPath)).toString();
  } else {
    throw new Error(`Game file not found: ${id}`);
  }

  return parseJSON5(raw) as GameExportV8;
}
