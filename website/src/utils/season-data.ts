import fs from 'node:fs';
import path from 'node:path';

type JsonMap = Record<string, any>;

const benchmarkResultsPath = path.join(process.cwd(), 'src', 'data', 'benchmark-results.json');
const personalitiesPath = path.join(process.cwd(), '..', 'puppeteer', 'personalities.json');

const tournamentCache = new Map<number, JsonMap>();
const seasonBenchmarkCache = new Map<number, JsonMap>();
let benchmarkResultsCache: JsonMap | undefined;
let personalitiesCache: JsonMap | undefined;

function readJsonFile<T>(filePath: string): T {
  return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as T;
}

function tournamentPathForSeason(season: number): string {
  return path.join(process.cwd(), '..', 'data', 'tournaments', `season-${season}.json`);
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

export function hasTournament(season: number): boolean {
  return fs.existsSync(tournamentPathForSeason(season));
}

export function hasDraft(season: number): boolean {
  if (!hasTournament(season)) {
    return false;
  }
  return !!loadTournament(season).draft;
}
