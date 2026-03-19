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

function makeV8Export(overrides = {}) {
  return {
    version: 8,
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
  it("loads normalized v8 exports", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json5": JSON.stringify(
        makeV8Export({
          players: [
            {
              name: "Alice",
              type: "pilot",
              deckName: "Azorius Control",
              toolCallsOk: 3,
              toolCallsFailed: 1,
              thinkingTimeSecs: 12.5,
            },
            {
              name: "Bob",
              type: "pilot",
              commander: "Omnath, Locus of Creation",
              toolCallsOk: 4,
              toolCallsFailed: 0,
              thinkingTimeSecs: 9.5,
            },
          ],
          annotations: [
            {
              decisionIndex: 0,
              snapshotIndex: 1,
              player: "Alice",
              type: "blunder",
              severity: "major",
              description: "Missed lethal",
              actionTaken: "Passed",
              betterLine: "Attack",
            },
            {
              decisionIndex: 1,
              snapshotIndex: 2,
              player: "Bob",
              type: "blunder",
              severity: "minor",
              description: "Tapped land suboptimally",
              actionTaken: "Cast spell",
              betterLine: "Use different land",
            },
          ],
          errors: [
            {
              ts: "00:00:03",
              player: "Alice",
              source: "pilot",
              message: "Tool call failed",
            },
          ],
        }),
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    const games = loadAllGames();

    expect(games).toHaveLength(1);
    expect(games[0].season).toBe(1);
    expect(games[0].players[0].toolCallsOk).toBe(3);
    expect(games[0].players[0].thinkingTimeSecs).toBe(12.5);
    expect(games[0].replayTitle).toBe(
      "Alice (Azorius Control) vs Bob (Omnath, Locus of Creation)",
    );
    expect(games[0].replayBlunderSummary).toEqual({
      total: 2,
      counts: {
        questionable: 0,
        minor: 1,
        moderate: 0,
        major: 1,
      },
    });
    expect(games[0].errors).toEqual([
      {
        ts: "00:00:03",
        player: "Alice",
        source: "pilot",
        message: "Tool call failed",
      },
    ]);
  });

  it("excludes questionable blunders from weighted scores", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json5": JSON.stringify(
        makeV8Export({
          totalTurns: 4,
          players: [
            {
              name: "Alice",
              type: "pilot",
              toolCallsOk: 3,
              toolCallsFailed: 1,
              thinkingTimeSecs: 12.5,
            },
            {
              name: "Bob",
              type: "pilot",
              toolCallsOk: 4,
              toolCallsFailed: 0,
              thinkingTimeSecs: 9.5,
            },
          ],
          annotations: [
            {
              decisionIndex: 0,
              snapshotIndex: 1,
              player: "Alice",
              type: "blunder",
              severity: "questionable",
              description: "Low-confidence nit",
              actionTaken: "Passed",
              betterLine: "Hold priority",
            },
            {
              decisionIndex: 1,
              snapshotIndex: 2,
              player: "Bob",
              type: "blunder",
              severity: "moderate",
              description: "Missed interaction",
              actionTaken: "Cast spell",
              betterLine: "Use removal first",
            },
          ],
        }),
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    const games = loadAllGames();

    expect(games[0].blunderScoreByPlayer).toEqual({
      Alice: 0,
      Bob: 0.5,
    });
  });

  it("rejects exports missing normalized player stats", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json5": JSON.stringify(
        makeV8Export({
          players: [{ name: "Alice", type: "pilot" }],
        }),
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    expect(() => loadAllGames()).toThrow(/missing toolCallsOk/);
  });

  it("rejects exports with invalid blunder severities", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json5": JSON.stringify(
        makeV8Export({
          annotations: [
            {
              decisionIndex: 0,
              snapshotIndex: 1,
              player: "Alice",
              type: "blunder",
              severity: "catastrophic",
              description: "Unexpected severity",
              actionTaken: "Passed",
              betterLine: "Attack",
            },
          ],
        }),
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    expect(() => loadAllGames()).toThrow(/invalid severity/);
  });
});
