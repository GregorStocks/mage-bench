import fs from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

const CACHE_KEY = Symbol.for("mage-bench:games-metadata");

function clearGamesCache() {
  delete globalThis[CACHE_KEY];
}

function mockGameFiles(files) {
  vi.spyOn(fs, "existsSync").mockReturnValue(true);
  vi.spyOn(fs, "readdirSync").mockReturnValue(Object.keys(files));
  vi.spyOn(fs, "readFileSync").mockImplementation((filePath) => {
    const name = String(filePath).split("/").pop();
    const contents = files[name];
    if (contents === undefined) {
      throw new Error(`Unexpected file read: ${filePath}`);
    }
    return contents;
  });
}

function makeV7Export(overrides = {}) {
  return {
    version: 7,
    id: "game_20260301_120000",
    timestamp: "2026-03-01T12:00:00-08:00",
    gameType: "Two Player Duel",
    deckType: "Constructed - Standard",
    totalTurns: 5,
    winner: "Alice",
    harnessEpoch: 40,
    youtubeUrl: "",
    players: [
      {
        name: "Alice",
        type: "pilot",
        toolCallsOk: 3,
        toolCallsFailed: 1,
        thinkingTimeSecs: 12.5,
      },
    ],
    cardImages: {},
    snapshots: [],
    actions: [],
    llmEvents: [],
    gameOver: null,
    annotations: [],
    blunderScriptVersion: 0,
    season: 1,
    tournament: null,
    ...overrides,
  };
}

afterEach(() => {
  clearGamesCache();
  vi.resetModules();
  vi.restoreAllMocks();
});

describe("loadAllGames", () => {
  it("loads normalized v7 exports", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json": JSON.stringify(makeV7Export()),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    const games = loadAllGames();

    expect(games).toHaveLength(1);
    expect(games[0].season).toBe(1);
    expect(games[0].players[0].toolCallsOk).toBe(3);
    expect(games[0].players[0].thinkingTimeSecs).toBe(12.5);
  });

  it("rejects exports missing normalized player stats", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json": JSON.stringify(
        makeV7Export({
          players: [{ name: "Alice", type: "pilot" }],
        }),
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    expect(() => loadAllGames()).toThrow(/missing toolCallsOk/);
  });
});
