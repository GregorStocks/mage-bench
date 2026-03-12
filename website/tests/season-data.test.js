import { describe, expect, it } from "vitest";

import {
  hasDraft,
  hasTournament,
  loadAvailableSeasons,
  loadPersonalities,
  loadSeasonBenchmark,
  loadTournament,
} from "../src/utils/season-data";

describe("season-data", () => {
  it("loads shared season assets from one utility module", () => {
    const seasons = loadAvailableSeasons();
    expect(seasons.length).toBeGreaterThan(0);

    const tournamentSeason = seasons.find((season) => hasTournament(season));
    expect(tournamentSeason).toBeDefined();
    if (tournamentSeason == null) {
      throw new Error("expected at least one season with tournament data");
    }

    const seasonBenchmark = loadSeasonBenchmark(tournamentSeason);
    expect(seasonBenchmark.totalGames).toBeGreaterThan(0);

    const tournament = loadTournament(tournamentSeason);
    expect(tournament.entrants.length).toBeGreaterThan(0);
    expect(hasDraft(tournamentSeason)).toBe(!!tournament.draft);

    const personalities = loadPersonalities();
    expect(Object.keys(personalities).length).toBeGreaterThan(0);
  });

  it("fails fast when a season benchmark file is missing", () => {
    expect(() => loadSeasonBenchmark(999999)).toThrow(/ENOENT|no such file/i);
  });

  it("fails fast when a tournament file is missing", () => {
    expect(() => loadTournament(999999)).toThrow(/ENOENT|no such file/i);
  });
});
