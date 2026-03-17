import type { GameEntry } from './load-games';
import { loadAllGames } from './load-games';
import {
  hasDraft,
  hasTournament,
  loadAvailableSeasons,
  loadTournament,
  type TournamentData,
  type TournamentEntrant,
} from './season-data';

export type SeasonStaticPath = { params: { season: string } };
export type TournamentMatchStaticPath = { params: { season: string; matchId: string } };
export type TournamentEntrantStaticPath = { params: { season: string; seed: string } };

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function buildSeasonStaticPaths(filter: (_season: number) => boolean): SeasonStaticPath[] {
  return loadAvailableSeasons()
    .filter(filter)
    .map((season) => ({ params: { season: String(season) } }));
}

export function buildAllSeasonStaticPaths(): SeasonStaticPath[] {
  return loadAvailableSeasons().map((season) => ({ params: { season: String(season) } }));
}

export function buildTournamentSeasonStaticPaths(): SeasonStaticPath[] {
  return buildSeasonStaticPaths(hasTournament);
}

export function buildDraftSeasonStaticPaths(): SeasonStaticPath[] {
  return buildSeasonStaticPaths(hasDraft);
}

export function buildTournamentMatchStaticPaths(): TournamentMatchStaticPath[] {
  const paths: TournamentMatchStaticPath[] = [];

  for (const season of loadAvailableSeasons()) {
    if (!hasTournament(season)) {
      continue;
    }
    const tournament = loadTournament(season);
    for (const round of tournament.rounds) {
      for (const match of round.matches) {
        if (match.winner_seed == null) {
          continue;
        }
        paths.push({
          params: {
            season: String(season),
            matchId: `r${round.round}m${match.match}`,
          },
        });
      }
    }
  }

  return paths;
}

export function buildTournamentEntrantStaticPaths(): TournamentEntrantStaticPath[] {
  const paths: TournamentEntrantStaticPath[] = [];

  for (const season of loadAvailableSeasons()) {
    if (!hasTournament(season)) {
      continue;
    }
    const tournament = loadTournament(season);
    for (const entrant of tournament.entrants) {
      paths.push({
        params: {
          season: String(season),
          seed: String(entrant.seed),
        },
      });
    }
  }

  return paths;
}

export function parseRequiredSeasonParam(seasonParam: string | undefined): number {
  invariant(seasonParam != null && seasonParam !== '', 'Missing season route param');
  invariant(/^\d+$/.test(seasonParam), `Invalid season route param: ${seasonParam}`);
  return parseInt(seasonParam, 10);
}

export function cleanJumpstartThemeName(name: string): string {
  return name.replace(/^J22 /, '');
}

export function formatJumpstartDeckName(halfDecks: string[]): string {
  return halfDecks.map(cleanJumpstartThemeName).join(' + ');
}

export function buildEntrantsBySeed(tournament: TournamentData): Map<number, TournamentEntrant> {
  return new Map<number, TournamentEntrant>(
    tournament.entrants.map((entrant) => [entrant.seed, entrant]),
  );
}

export function loadSeasonTournamentContext(season: number): {
  tournament: TournamentData;
  hasDraft: boolean;
  entrantsBySeed: Map<number, TournamentEntrant>;
} {
  const tournament = loadTournament(season);
  return {
    tournament,
    hasDraft: !!tournament.draft,
    entrantsBySeed: buildEntrantsBySeed(tournament),
  };
}

export function getTournamentDeckName(tournament: TournamentData, seed: number | null): string | null {
  if (seed == null) {
    return null;
  }
  const decklist = tournament.draft?.decklists[String(seed)];
  if (decklist == null) {
    return null;
  }
  return formatJumpstartDeckName(decklist.half_decks);
}

export function loadSeasonGameEntries(season: number): GameEntry[] {
  return loadAllGames().filter((game) => game.season === season);
}

export function loadSeasonGameMap(season: number): Map<string, GameEntry> {
  return new Map<string, GameEntry>(
    loadSeasonGameEntries(season).map((game) => [game.id, game]),
  );
}
