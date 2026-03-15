import fs from 'node:fs';
import path from 'node:path';

type JsonObject = Record<string, unknown>;

export type SeasonPhase = 'regular-season' | 'tournament' | 'between-seasons';

export interface SeasonState {
  currentSeason: number;
  phase: SeasonPhase;
  tournamentPath: string | null;
}

export interface PersonalityDefinition {
  prompt_suffix: string;
  name_part?: string;
  expressive?: boolean;
}

type PersonalitiesData = Record<string, PersonalityDefinition>;

export interface BenchmarkModel {
  modelId: string;
  modelName: string;
  provider: string;
  rating: number | null;
  gamesPlayed: number;
  winRate: number;
  timeoutLosses: number;
  timeoutLossRate: number;
  avgApiCost: number;
  avgToolCallsOk: number;
  avgToolCallsFailed: number;
  avgThinkingTimeSecs: number;
  blunderScore: number;
  reasoningEffort?: string;
  inactive?: string;
}

export interface BenchmarkFormat {
  generatedAt?: string;
  totalGames: number;
  models: BenchmarkModel[];
  exhibition?: boolean;
}

export interface BenchmarkResults {
  availableSeasons: number[];
  formats: Record<string, BenchmarkFormat>;
  generatedAt: string;
  minBlunderVersion: number;
  models: BenchmarkModel[];
  totalGames: number;
}

export interface TournamentGameResult {
  game_id: string;
  winner_seed: number | null;
}

export interface TournamentMatch {
  match: number | null;
  seed_a: number | null;
  seed_b: number | null;
  winner_seed: number | null;
  games: TournamentGameResult[];
}

export interface TournamentRound {
  round: number;
  name: string;
  matches: TournamentMatch[];
}

export interface TournamentEntrant {
  seed: number;
  preset: string;
  model: string;
  display_name: string;
  personality: string;
  combined_elo: number;
  games_played: number;
  reasoning_effort?: string | null;
}

export interface TournamentDeckCard {
  name: string;
  count: number;
  set?: string;
  num?: string;
}

export interface TournamentDecklist {
  half_decks: string[];
  cards: string[];
}

export interface TournamentDraftUsage {
  prompt_tokens: number;
  completion_tokens: number;
}

export interface TournamentDraftAttempt {
  attempt: number;
  response?: string;
  accepted: boolean;
  thinking?: string;
  rejection_reason?: string;
}

export interface TournamentDraftPick {
  seed: number;
  round: number;
  options: string[];
  picked: string;
  reasoning: string;
  usage: TournamentDraftUsage;
  cost_usd: number;
  attempts: TournamentDraftAttempt[];
}

export interface TournamentDraft {
  packs_per_player: number;
  pool: string[];
  picks: TournamentDraftPick[];
  decklists: Record<string, TournamentDecklist>;
  total_cost_usd: number;
  history_version: number;
}

export interface TournamentData {
  season: number;
  size: number;
  elimination: string;
  best_of: number;
  champion_seed?: number | null;
  created_at: string;
  completed_at?: string | null;
  entrants: TournamentEntrant[];
  rounds: TournamentRound[];
  draft?: TournamentDraft;
}

const benchmarkResultsPath = path.join(process.cwd(), 'src', 'data', 'benchmark-results.json');
const seasonStatePath = path.join(process.cwd(), 'src', 'data', 'season.json');
const personalitiesPath = path.join(process.cwd(), '..', 'puppeteer', 'personalities.json');

const tournamentCache = new Map<number, TournamentData>();
const seasonBenchmarkCache = new Map<number, BenchmarkResults>();
let benchmarkResultsCache: BenchmarkResults | undefined;
let seasonStateCache: SeasonState | undefined;
let personalitiesCache: PersonalitiesData | undefined;

function readJsonFile(filePath: string): unknown {
  return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as unknown;
}

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function assertRecord(value: unknown, message: string): asserts value is JsonObject {
  invariant(value != null && typeof value === 'object' && !Array.isArray(value), message);
}

function readRequiredString(candidate: JsonObject, key: string, message: string): string {
  const value = candidate[key];
  invariant(typeof value === 'string', message);
  return value;
}

function readOptionalString(candidate: JsonObject, key: string, message: string): string | undefined {
  const value = candidate[key];
  invariant(value === undefined || typeof value === 'string', message);
  return value;
}

function readOptionalNullableString(candidate: JsonObject, key: string, message: string): string | null | undefined {
  const value = candidate[key];
  invariant(value === undefined || value === null || typeof value === 'string', message);
  return value as string | null | undefined;
}

function readRequiredInteger(candidate: JsonObject, key: string, message: string): number {
  const value = candidate[key];
  invariant(Number.isInteger(value), message);
  return value as number;
}

function readOptionalNullableInteger(candidate: JsonObject, key: string, message: string): number | null | undefined {
  const value = candidate[key];
  invariant(value === undefined || value === null || Number.isInteger(value), message);
  return value as number | null | undefined;
}

function readRequiredNumber(candidate: JsonObject, key: string, message: string): number {
  const value = candidate[key];
  invariant(typeof value === 'number', message);
  return value;
}

function readOptionalBoolean(candidate: JsonObject, key: string, message: string): boolean | undefined {
  const value = candidate[key];
  invariant(value === undefined || typeof value === 'boolean', message);
  return value;
}

function readRequiredBoolean(candidate: JsonObject, key: string, message: string): boolean {
  const value = candidate[key];
  invariant(typeof value === 'boolean', message);
  return value;
}

function readRequiredStringArray(candidate: JsonObject, key: string, message: string): string[] {
  const value = candidate[key];
  invariant(Array.isArray(value), message);
  invariant(value.every(item => typeof item === 'string'), message);
  return value;
}

function readRequiredIntegerArray(candidate: JsonObject, key: string, message: string): number[] {
  const value = candidate[key];
  invariant(Array.isArray(value), message);
  invariant(value.every(item => Number.isInteger(item)), message);
  return value as number[];
}

type ArrayItemParser<T> = (..._args: [unknown, number]) => T;

function readRequiredArray<T>(
  candidate: JsonObject,
  key: string,
  message: string,
  parseItem: ArrayItemParser<T>,
): T[] {
  const value = candidate[key];
  invariant(Array.isArray(value), message);
  return value.map((item, index) => parseItem(item, index));
}

function parseBenchmarkModel(data: unknown, context = 'Benchmark model'): BenchmarkModel {
  assertRecord(data, `${context} must be an object`);
  const model: BenchmarkModel = {
    modelId: readRequiredString(data, 'modelId', `${context} missing modelId`),
    modelName: readRequiredString(data, 'modelName', `${context} missing modelName`),
    provider: readRequiredString(data, 'provider', `${context} missing provider`),
    rating: readOptionalNullableInteger(data, 'rating', `${context} has invalid rating`) ?? null,
    gamesPlayed: readRequiredInteger(data, 'gamesPlayed', `${context} missing gamesPlayed`),
    winRate: readRequiredNumber(data, 'winRate', `${context} missing winRate`),
    timeoutLosses: readRequiredInteger(data, 'timeoutLosses', `${context} missing timeoutLosses`),
    timeoutLossRate: readRequiredNumber(data, 'timeoutLossRate', `${context} missing timeoutLossRate`),
    avgApiCost: readRequiredNumber(data, 'avgApiCost', `${context} missing avgApiCost`),
    avgToolCallsOk: readRequiredNumber(data, 'avgToolCallsOk', `${context} missing avgToolCallsOk`),
    avgToolCallsFailed: readRequiredNumber(data, 'avgToolCallsFailed', `${context} missing avgToolCallsFailed`),
    avgThinkingTimeSecs: readRequiredNumber(data, 'avgThinkingTimeSecs', `${context} missing avgThinkingTimeSecs`),
    blunderScore: readRequiredNumber(data, 'blunderScore', `${context} missing blunderScore`),
  };

  const reasoningEffort = readOptionalString(data, 'reasoningEffort', `${context} has invalid reasoningEffort`);
  if (reasoningEffort !== undefined) {
    model.reasoningEffort = reasoningEffort;
  }

  const inactive = readOptionalString(data, 'inactive', `${context} has invalid inactive marker`);
  if (inactive !== undefined) {
    model.inactive = inactive;
  }

  return model;
}

function parseBenchmarkFormat(data: unknown, context = 'Benchmark format'): BenchmarkFormat {
  assertRecord(data, `${context} must be an object`);
  const format: BenchmarkFormat = {
    totalGames: readRequiredInteger(data, 'totalGames', `${context} missing totalGames`),
    models: readRequiredArray(
      data,
      'models',
      `${context} models must be an array`,
      (item, index) => parseBenchmarkModel(item, `${context} model ${index}`),
    ),
  };

  const generatedAt = readOptionalString(data, 'generatedAt', `${context} has invalid generatedAt`);
  if (generatedAt !== undefined) {
    format.generatedAt = generatedAt;
  }

  const exhibition = readOptionalBoolean(data, 'exhibition', `${context} has invalid exhibition flag`);
  if (exhibition !== undefined) {
    format.exhibition = exhibition;
  }

  return format;
}

export function parseBenchmarkResults(data: unknown): BenchmarkResults {
  assertRecord(data, 'Benchmark results must be an object');
  const formatsValue = data.formats;
  assertRecord(formatsValue, 'Benchmark results missing formats record');

  const formats: Record<string, BenchmarkFormat> = {};
  for (const [name, format] of Object.entries(formatsValue)) {
    formats[name] = parseBenchmarkFormat(format, `Benchmark format ${name}`);
  }

  return {
    availableSeasons: readRequiredIntegerArray(data, 'availableSeasons', 'Benchmark results missing availableSeasons'),
    formats,
    generatedAt: readRequiredString(data, 'generatedAt', 'Benchmark results missing generatedAt'),
    minBlunderVersion: readRequiredInteger(data, 'minBlunderVersion', 'Benchmark results missing minBlunderVersion'),
    models: readRequiredArray(
      data,
      'models',
      'Benchmark results models must be an array',
      (item, index) => parseBenchmarkModel(item, `Benchmark model ${index}`),
    ),
    totalGames: readRequiredInteger(data, 'totalGames', 'Benchmark results missing totalGames'),
  };
}

function parsePersonalityDefinition(data: unknown, context = 'Personality definition'): PersonalityDefinition {
  assertRecord(data, `${context} must be an object`);
  const personality: PersonalityDefinition = {
    prompt_suffix: readRequiredString(data, 'prompt_suffix', `${context} missing prompt_suffix`),
  };

  const namePart = readOptionalString(data, 'name_part', `${context} has invalid name_part`);
  if (namePart !== undefined) {
    personality.name_part = namePart;
  }

  const expressive = readOptionalBoolean(data, 'expressive', `${context} has invalid expressive`);
  if (expressive !== undefined) {
    personality.expressive = expressive;
  }

  return personality;
}

function parsePersonalities(data: unknown): PersonalitiesData {
  assertRecord(data, 'Personalities data must be an object');
  const personalities: PersonalitiesData = {};
  for (const [name, personality] of Object.entries(data)) {
    personalities[name] = parsePersonalityDefinition(personality, `Personality ${name}`);
  }
  return personalities;
}

function parseTournamentGameResult(data: unknown, context = 'Tournament game'): TournamentGameResult {
  assertRecord(data, `${context} must be an object`);
  return {
    game_id: readRequiredString(data, 'game_id', `${context} missing game_id`),
    winner_seed: readOptionalNullableInteger(data, 'winner_seed', `${context} has invalid winner_seed`) ?? null,
  };
}

function parseTournamentMatch(data: unknown, context = 'Tournament match'): TournamentMatch {
  assertRecord(data, `${context} must be an object`);
  return {
    match: readOptionalNullableInteger(data, 'match', `${context} has invalid match`) ?? null,
    seed_a: readOptionalNullableInteger(data, 'seed_a', `${context} has invalid seed_a`) ?? null,
    seed_b: readOptionalNullableInteger(data, 'seed_b', `${context} has invalid seed_b`) ?? null,
    winner_seed: readOptionalNullableInteger(data, 'winner_seed', `${context} has invalid winner_seed`) ?? null,
    games: readRequiredArray(
      data,
      'games',
      `${context} games must be an array`,
      (item, index) => parseTournamentGameResult(item, `${context} game ${index}`),
    ),
  };
}

function parseTournamentRound(data: unknown, context = 'Tournament round'): TournamentRound {
  assertRecord(data, `${context} must be an object`);
  return {
    round: readRequiredInteger(data, 'round', `${context} missing round`),
    name: readRequiredString(data, 'name', `${context} missing name`),
    matches: readRequiredArray(
      data,
      'matches',
      `${context} matches must be an array`,
      (item, index) => parseTournamentMatch(item, `${context} match ${index}`),
    ),
  };
}

function parseTournamentEntrant(data: unknown, context = 'Tournament entrant'): TournamentEntrant {
  assertRecord(data, `${context} must be an object`);
  return {
    seed: readRequiredInteger(data, 'seed', `${context} missing seed`),
    preset: readRequiredString(data, 'preset', `${context} missing preset`),
    model: readRequiredString(data, 'model', `${context} missing model`),
    display_name: readRequiredString(data, 'display_name', `${context} missing display_name`),
    personality: readRequiredString(data, 'personality', `${context} missing personality`),
    combined_elo: readRequiredNumber(data, 'combined_elo', `${context} missing combined_elo`),
    games_played: readRequiredInteger(data, 'games_played', `${context} missing games_played`),
    reasoning_effort:
      readOptionalNullableString(data, 'reasoning_effort', `${context} has invalid reasoning_effort`) ?? undefined,
  };
}

export function parseDecklistCardEntry(cardLine: string): TournamentDeckCard {
  const match = cardLine.match(/^(\d+)\s+\[(\w+):(\w+)\]\s+(.+)$/);
  if (match == null) {
    return { count: 1, name: cardLine };
  }

  return {
    count: Number.parseInt(match[1], 10),
    set: match[2],
    num: match[3],
    name: match[4],
  };
}

function parseTournamentDecklist(data: unknown, context = 'Tournament decklist'): TournamentDecklist {
  assertRecord(data, `${context} must be an object`);
  return {
    half_decks: readRequiredStringArray(data, 'half_decks', `${context} half_decks must be an array of strings`),
    cards: readRequiredStringArray(data, 'cards', `${context} cards must be an array of strings`),
  };
}

function parseTournamentDraftUsage(data: unknown, context = 'Tournament draft usage'): TournamentDraftUsage {
  assertRecord(data, `${context} must be an object`);
  return {
    prompt_tokens: readRequiredNumber(data, 'prompt_tokens', `${context} missing prompt_tokens`),
    completion_tokens: readRequiredNumber(data, 'completion_tokens', `${context} missing completion_tokens`),
  };
}

function parseTournamentDraftAttempt(data: unknown, context = 'Tournament draft attempt'): TournamentDraftAttempt {
  assertRecord(data, `${context} must be an object`);
  const attempt: TournamentDraftAttempt = {
    attempt: readRequiredInteger(data, 'attempt', `${context} missing attempt`),
    accepted: readRequiredBoolean(data, 'accepted', `${context} missing accepted`),
  };

  const response = readOptionalString(data, 'response', `${context} has invalid response`);
  if (response !== undefined) {
    attempt.response = response;
  }

  const thinking = readOptionalString(data, 'thinking', `${context} has invalid thinking`);
  if (thinking !== undefined) {
    attempt.thinking = thinking;
  }

  const rejectionReason = readOptionalString(data, 'rejection_reason', `${context} has invalid rejection_reason`);
  if (rejectionReason !== undefined) {
    attempt.rejection_reason = rejectionReason;
  }

  return attempt;
}

function parseTournamentDraftPick(data: unknown, context = 'Tournament draft pick'): TournamentDraftPick {
  assertRecord(data, `${context} must be an object`);
  return {
    seed: readRequiredInteger(data, 'seed', `${context} missing seed`),
    round: readRequiredInteger(data, 'round', `${context} missing round`),
    options: readRequiredStringArray(data, 'options', `${context} options must be an array of strings`),
    picked: readRequiredString(data, 'picked', `${context} missing picked`),
    reasoning: readRequiredString(data, 'reasoning', `${context} missing reasoning`),
    usage: parseTournamentDraftUsage(data.usage, `${context} usage`),
    cost_usd: readRequiredNumber(data, 'cost_usd', `${context} missing cost_usd`),
    attempts: readRequiredArray(
      data,
      'attempts',
      `${context} attempts must be an array`,
      (item, index) => parseTournamentDraftAttempt(item, `${context} attempt ${index}`),
    ),
  };
}

function parseTournamentDraft(data: unknown): TournamentDraft {
  assertRecord(data, 'Tournament draft must be an object');
  const decklistsValue = data.decklists;
  assertRecord(decklistsValue, 'Tournament draft missing decklists');

  const decklists: Record<string, TournamentDecklist> = {};
  for (const [seed, decklist] of Object.entries(decklistsValue)) {
    decklists[seed] = parseTournamentDecklist(decklist, `Tournament draft decklist ${seed}`);
  }

  return {
    packs_per_player: readRequiredInteger(data, 'packs_per_player', 'Tournament draft missing packs_per_player'),
    pool: readRequiredStringArray(data, 'pool', 'Tournament draft pool must be an array of strings'),
    picks: readRequiredArray(
      data,
      'picks',
      'Tournament draft picks must be an array',
      (item, index) => parseTournamentDraftPick(item, `Tournament draft pick ${index}`),
    ),
    decklists,
    total_cost_usd: readRequiredNumber(data, 'total_cost_usd', 'Tournament draft missing total_cost_usd'),
    history_version: readRequiredInteger(data, 'history_version', 'Tournament draft missing history_version'),
  };
}

export function parseTournament(data: unknown): TournamentData {
  assertRecord(data, 'Tournament data must be an object');
  const tournament: TournamentData = {
    season: readRequiredInteger(data, 'season', 'Tournament data missing season'),
    size: readRequiredInteger(data, 'size', 'Tournament data missing size'),
    elimination: readRequiredString(data, 'elimination', 'Tournament data missing elimination'),
    best_of: readRequiredInteger(data, 'best_of', 'Tournament data missing best_of'),
    created_at: readRequiredString(data, 'created_at', 'Tournament data missing created_at'),
    entrants: readRequiredArray(
      data,
      'entrants',
      'Tournament data entrants must be an array',
      (item, index) => parseTournamentEntrant(item, `Tournament entrant ${index}`),
    ),
    rounds: readRequiredArray(
      data,
      'rounds',
      'Tournament data rounds must be an array',
      (item, index) => parseTournamentRound(item, `Tournament round ${index}`),
    ),
  };

  const championSeed = readOptionalNullableInteger(data, 'champion_seed', 'Tournament data has invalid champion_seed');
  if (championSeed !== undefined) {
    tournament.champion_seed = championSeed;
  }

  const completedAt = readOptionalNullableString(data, 'completed_at', 'Tournament data has invalid completed_at');
  if (completedAt !== undefined) {
    tournament.completed_at = completedAt;
  }

  if (data.draft !== undefined) {
    tournament.draft = parseTournamentDraft(data.draft);
  }

  return tournament;
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
  assertRecord(data, 'Season data must be an object');
  const candidate = data;
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

export function loadBenchmarkResults(): BenchmarkResults {
  if (benchmarkResultsCache == null) {
    benchmarkResultsCache = parseBenchmarkResults(readJsonFile(benchmarkResultsPath));
  }
  return benchmarkResultsCache;
}

export function loadAvailableSeasons(): number[] {
  return loadBenchmarkResults().availableSeasons;
}

export function loadSeasonState(): SeasonState {
  if (seasonStateCache == null) {
    seasonStateCache = parseSeasonState(readJsonFile(seasonStatePath));
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

export function loadTournament(season: number): TournamentData {
  const cached = tournamentCache.get(season);
  if (cached != null) {
    return cached;
  }

  const tournament = parseTournament(readJsonFile(tournamentPathForSeason(season)));
  tournamentCache.set(season, tournament);
  return tournament;
}

export function loadSeasonBenchmark(season: number): BenchmarkResults {
  const cached = seasonBenchmarkCache.get(season);
  if (cached != null) {
    return cached;
  }

  const benchmark = parseBenchmarkResults(readJsonFile(seasonBenchmarkPathForSeason(season)));
  seasonBenchmarkCache.set(season, benchmark);
  return benchmark;
}

export function loadPersonalities(): PersonalitiesData {
  if (personalitiesCache == null) {
    personalitiesCache = parsePersonalities(readJsonFile(personalitiesPath));
  }
  return personalitiesCache;
}

export function loadLatestCompletedTournament(): { season: number; tournament: TournamentData } | null {
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
      tournament: parseTournament(readJsonFile(resolveRepoRelativePath(seasonState.tournamentPath))),
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
