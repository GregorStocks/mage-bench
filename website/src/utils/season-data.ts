import fs from 'node:fs';
import path from 'node:path';

type JsonMap = Record<string, any>;
export type SeasonPhase = 'regular-season' | 'tournament' | 'between-seasons';

export interface SeasonState {
  currentSeason: number;
  phase: SeasonPhase;
  tournamentPath: string | null;
}

const benchmarkResultsPath = path.join(process.cwd(), 'src', 'data', 'benchmark-results.json');
const seasonStatePath = path.join(process.cwd(), 'src', 'data', 'season.json');
const personalitiesPath = path.join(process.cwd(), '..', 'puppeteer', 'personalities.json');

const tournamentCache = new Map<number, JsonMap>();
const seasonBenchmarkCache = new Map<number, JsonMap>();
let benchmarkResultsCache: JsonMap | undefined;
let seasonStateCache: SeasonState | undefined;
let personalitiesCache: JsonMap | undefined;

function readJsonFile<T>(filePath: string): T {
  return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as T;
}

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function tournamentPathForSeason(season: number): string {
  return path.join(process.cwd(), '..', 'data', 'tournaments', `season-${season}.json`);
}

function resolveRepoRelativePath(relativePath: string): string {
  invariant(!path.isAbsolute(relativePath), `Expected repo-relative path, got absolute path: ${relativePath}`);
  return path.join(process.cwd(), '..', relativePath);
}

function seasonFromTournamentPath(tournamentPath: string): number {
  const normalized = tournamentPath.replaceAll('\\', '/');
  const match = normalized.match(/^data\/tournaments\/season-(\d+)\.json$/);
  invariant(match != null, `Invalid tournament path in season data: ${tournamentPath}`);
  return Number.parseInt(match[1], 10);
}

function parseSeasonState(data: unknown): SeasonState {
  invariant(data != null && typeof data === 'object', 'Season data must be an object');

  const candidate = data as Record<string, unknown>;
  invariant(Number.isInteger(candidate.current_season), 'Season data missing integer current_season');
  invariant(typeof candidate.phase === 'string', 'Season data missing phase');
  invariant(candidate.tournament === null || typeof candidate.tournament === 'string', 'Season data has invalid tournament path');
  invariant(
    candidate.phase === 'regular-season' ||
      candidate.phase === 'tournament' ||
      candidate.phase === 'between-seasons',
    `Unsupported season phase: ${String(candidate.phase)}`,
  );

  return {
    currentSeason: candidate.current_season as number,
    phase: candidate.phase,
    tournamentPath: candidate.tournament as string | null,
  };
}

function seasonBenchmarkPathForSeason(season: number): string {
  return path.join(process.cwd(), 'public', 'data', `benchmark-results-season-${season}.json`);
}

function loadBenchmarkResults(): JsonMap {
  if (benchmarkResultsCache == null) {
    benchmarkResultsCache = readJsonFile<JsonMap>(benchmarkResultsPath);
  }
  return benchmarkResultsCache;
}

export function loadAvailableSeasons(): number[] {
  return loadBenchmarkResults().availableSeasons ?? [];
}

export function loadSeasonState(): SeasonState {
  if (seasonStateCache == null) {
    seasonStateCache = parseSeasonState(readJsonFile<unknown>(seasonStatePath));
  }
  return seasonStateCache;
}

export function getLatestCompletedChampionSeason(seasonState: SeasonState): number | null {
  if (seasonState.phase === 'regular-season') {
    invariant(
      seasonState.tournamentPath == null,
      `Season ${seasonState.currentSeason} regular-season must not keep an active tournament path`,
    );
    return seasonState.currentSeason > 1 ? seasonState.currentSeason - 1 : null;
  }

  invariant(
    seasonState.tournamentPath != null,
    `Season ${seasonState.currentSeason} ${seasonState.phase} requires an active tournament path`,
  );
  const activeTournamentSeason = seasonFromTournamentPath(seasonState.tournamentPath);
  invariant(
    activeTournamentSeason === seasonState.currentSeason,
    `Season ${seasonState.currentSeason} ${seasonState.phase} must point at season-${seasonState.currentSeason}.json, got ${seasonState.tournamentPath}`,
  );

  if (seasonState.phase === 'between-seasons') {
    return activeTournamentSeason;
  }

  return activeTournamentSeason > 1 ? activeTournamentSeason - 1 : null;
}

export function loadTournament(season: number): JsonMap {
  const cached = tournamentCache.get(season);
  if (cached != null) {
    return cached;
  }

  const tournament = readJsonFile<JsonMap>(tournamentPathForSeason(season));
  tournamentCache.set(season, tournament);
  return tournament;
}

export function loadSeasonBenchmark(season: number): JsonMap {
  const cached = seasonBenchmarkCache.get(season);
  if (cached != null) {
    return cached;
  }

  const benchmark = readJsonFile<JsonMap>(seasonBenchmarkPathForSeason(season));
  seasonBenchmarkCache.set(season, benchmark);
  return benchmark;
}

export function loadPersonalities(): JsonMap {
  if (personalitiesCache == null) {
    personalitiesCache = readJsonFile<JsonMap>(personalitiesPath);
  }
  return personalitiesCache;
}

export function loadLatestCompletedTournament(): { season: number; tournament: JsonMap } | null {
  const seasonState = loadSeasonState();
  const championSeason = getLatestCompletedChampionSeason(seasonState);
  if (championSeason == null) {
    return null;
  }

  if (seasonState.phase === 'between-seasons') {
    invariant(
      seasonState.tournamentPath != null,
      `Season ${seasonState.currentSeason} between-seasons requires an active tournament path`,
    );
    return {
      season: championSeason,
      tournament: readJsonFile<JsonMap>(resolveRepoRelativePath(seasonState.tournamentPath)),
    };
  }

  return {
    season: championSeason,
    tournament: loadTournament(championSeason),
  };
}

export function hasTournament(season: number): boolean {
  return fs.existsSync(tournamentPathForSeason(season));
}

export function hasDraft(season: number): boolean {
  if (!hasTournament(season)) {
    return false;
  }
  return !!loadTournament(season).draft;
}
