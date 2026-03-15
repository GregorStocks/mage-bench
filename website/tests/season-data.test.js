import { describe, expect, it } from "vitest";

import {
  getLatestCompletedChampionSeason,
  hasDraft,
  hasTournament,
  loadAvailableSeasons,
  loadBenchmarkResults,
  loadLatestCompletedTournament,
  loadPersonalities,
  loadSeasonBenchmark,
  loadSeasonState,
  loadTournament,
  parseBenchmarkResults,
  parseTournament,
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
    expect(typeof personalities.villain.prompt_suffix).toBe("string");

    const benchmarkResults = loadBenchmarkResults();
    expect(benchmarkResults.minBlunderVersion).toBeGreaterThan(0);
  });

  it("fails fast when a season benchmark file is missing", () => {
    expect(() => loadSeasonBenchmark(999999)).toThrow(/ENOENT|no such file/i);
  });

  it("fails fast when a tournament file is missing", () => {
    expect(() => loadTournament(999999)).toThrow(/ENOENT|no such file/i);
  });

  it("resolves the homepage champion season from season state", () => {
    expect(
      getLatestCompletedChampionSeason({
        currentSeason: 1,
        phase: "between-seasons",
        tournamentPath: "data/tournaments/season-1.json",
      })
    ).toBe(1);

    expect(
      getLatestCompletedChampionSeason({
        currentSeason: 2,
        phase: "regular-season",
        tournamentPath: null,
      })
    ).toBe(1);

    expect(
      getLatestCompletedChampionSeason({
        currentSeason: 2,
        phase: "tournament",
        tournamentPath: "data/tournaments/season-2.json",
      })
    ).toBe(1);

    expect(
      getLatestCompletedChampionSeason({
        currentSeason: 1,
        phase: "regular-season",
        tournamentPath: null,
      })
    ).toBeNull();
  });

  it("fails fast on inconsistent season states for the homepage champion", () => {
    expect(() =>
      getLatestCompletedChampionSeason({
        currentSeason: 2,
        phase: "regular-season",
        tournamentPath: "data/tournaments/season-2.json",
      })
    ).toThrow(/must not keep an active tournament path/i);

    expect(() =>
      getLatestCompletedChampionSeason({
        currentSeason: 2,
        phase: "between-seasons",
        tournamentPath: null,
      })
    ).toThrow(/requires an active tournament path/i);

    expect(() =>
      getLatestCompletedChampionSeason({
        currentSeason: 2,
        phase: "tournament",
        tournamentPath: "data/tournaments/season-1.json",
      })
    ).toThrow(/must point at season-2\.json/i);
  });

  it("loads the latest completed tournament from current season data", () => {
    const seasonState = loadSeasonState();
    const championship = loadLatestCompletedTournament();
    const championSeason = getLatestCompletedChampionSeason(seasonState);

    expect(championship == null).toBe(championSeason == null);
    if (championship == null || championSeason == null) {
      return;
    }

    expect(championship.season).toBe(championSeason);
    expect(championship.tournament.entrants.length).toBeGreaterThan(0);
    expect(championship.tournament.rounds.length).toBeGreaterThan(0);
  });

  it("fails fast on malformed benchmark results", () => {
    const benchmark = structuredClone(loadBenchmarkResults());
    delete benchmark.minBlunderVersion;

    expect(() => parseBenchmarkResults(benchmark)).toThrow(/minBlunderVersion/i);
  });

  it("fails fast on malformed tournament rounds", () => {
    const tournament = structuredClone(loadTournament(1));
    tournament.rounds[0].matches[0].match = "oops";

    expect(() => parseTournament(tournament)).toThrow(/invalid match/i);
  });
});
