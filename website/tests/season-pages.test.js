import { describe, expect, it } from "vitest";

import {
  buildAllSeasonStaticPaths,
  buildDraftSeasonStaticPaths,
  buildTournamentEntrantStaticPaths,
  buildTournamentMatchStaticPaths,
  buildTournamentSeasonStaticPaths,
  cleanJumpstartThemeName,
  formatJumpstartDeckName,
  getTournamentDeckName,
  loadSeasonGameEntries,
  loadSeasonGameMap,
  loadSeasonTournamentContext,
  parseRequiredSeasonParam,
} from "../src/utils/season-pages";
import { hasDraft, hasTournament, loadAvailableSeasons, loadTournament } from "../src/utils/season-data";

describe("season-pages", () => {
  it("builds season static paths from shared season data", () => {
    const seasons = loadAvailableSeasons();

    expect(buildAllSeasonStaticPaths()).toEqual(
      seasons.map((season) => ({ params: { season: String(season) } }))
    );
    expect(buildTournamentSeasonStaticPaths()).toEqual(
      seasons
        .filter((season) => hasTournament(season))
        .map((season) => ({ params: { season: String(season) } }))
    );
    expect(buildDraftSeasonStaticPaths()).toEqual(
      seasons
        .filter((season) => hasDraft(season))
        .map((season) => ({ params: { season: String(season) } }))
    );
  });

  it("builds tournament entrant and match paths from tournament data", () => {
    const tournamentSeasons = loadAvailableSeasons().filter((season) => hasTournament(season));
    const expectedEntrantCount = tournamentSeasons.reduce(
      (count, season) => count + loadTournament(season).entrants.length,
      0
    );
    const expectedMatchCount = tournamentSeasons.reduce(
      (count, season) =>
        count +
        loadTournament(season).rounds.reduce(
          (roundCount, round) =>
            roundCount + round.matches.filter((match) => match.winner_seed != null).length,
          0
        ),
      0
    );

    expect(buildTournamentEntrantStaticPaths()).toHaveLength(expectedEntrantCount);
    expect(buildTournamentMatchStaticPaths()).toHaveLength(expectedMatchCount);
  });

  it("parses season params and fails fast on invalid values", () => {
    expect(parseRequiredSeasonParam("12")).toBe(12);
    expect(() => parseRequiredSeasonParam(undefined)).toThrow(/Missing season route param/);
    expect(() => parseRequiredSeasonParam("12x")).toThrow(/Invalid season route param/);
  });

  it("formats Jumpstart theme names and tournament deck labels", () => {
    const draftSeason = loadAvailableSeasons().find((season) => hasDraft(season));
    expect(draftSeason).toBeDefined();
    if (draftSeason == null) {
      throw new Error("expected at least one season with draft data");
    }

    const tournament = loadTournament(draftSeason);
    if (tournament.draft == null) {
      throw new Error(`expected season ${draftSeason} to have draft data`);
    }

    const [seed, decklist] = Object.entries(tournament.draft.decklists)[0] ?? [];
    if (seed == null || decklist == null) {
      throw new Error(`expected season ${draftSeason} to have at least one drafted deck`);
    }

    expect(cleanJumpstartThemeName("J22 Cats")).toBe("Cats");
    expect(cleanJumpstartThemeName("Cats")).toBe("Cats");
    expect(formatJumpstartDeckName(["J22 Cats", "Dogs"])).toBe("Cats + Dogs");
    expect(getTournamentDeckName(tournament, Number(seed))).toBe(
      formatJumpstartDeckName(decklist.half_decks)
    );
    expect(getTournamentDeckName(tournament, null)).toBeNull();
  });

  it("loads shared tournament context and season game metadata", () => {
    const seasons = loadAvailableSeasons();
    const season = seasons[0];
    const context = loadSeasonTournamentContext(
      seasons.find((candidate) => hasTournament(candidate)) ?? season
    );
    const seasonGames = loadSeasonGameEntries(season);
    const gameMap = loadSeasonGameMap(season);

    expect(context.entrantsBySeed.size).toBe(context.tournament.entrants.length);
    expect(context.hasDraft).toBe(!!context.tournament.draft);
    expect(seasonGames.length).toBeGreaterThan(0);
    expect(gameMap.size).toBe(seasonGames.length);
    expect(gameMap.get(seasonGames[0].id)).toEqual(seasonGames[0]);
  }, 30000);
});
