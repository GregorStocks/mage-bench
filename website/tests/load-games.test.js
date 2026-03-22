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

function makeV9Export(overrides = {}) {
  return {
    version: 9,
    id: "game_20260301_120000",
    timestamp: "2026-03-01T12:00:00-08:00",
    game_type: "Two Player Duel",
    deck_type: "Constructed - Standard",
    total_turns: 5,
    winner: "Alice",
    harness_epoch: 40,
    youtube_url: "",
    players: [
      {
        name: "Alice",
        type: "pilot",
        tool_calls_ok: 3,
        tool_calls_failed: 1,
        thinking_time_secs: 12.5,
      },
    ],
    card_images: {},
    snapshots: [],
    actions: [],
    llm_events: [],
    game_over: null,
    annotations: [],
    blunder_script_version: 0,
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
  it("rejects legacy v8 exports", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json5": JSON.stringify(
        {
          version: 8,
          id: "game_20260301_120000",
          timestamp: "2026-03-01T12:00:00-08:00",
          gameType: "Two Player Duel",
          deckType: "Constructed - Standard",
          totalTurns: 5,
          winner: "Alice",
          harnessEpoch: 40,
          players: [
            {
              name: "Alice",
              type: "pilot",
              toolCallsOk: 3,
              toolCallsFailed: 1,
              thinkingTimeSecs: 12.5,
            },
          ],
          youtubeUrl: "",
          cardImages: {},
          snapshots: [],
          actions: [],
          llmEvents: [],
          gameOver: null,
          annotations: [],
          blunderScriptVersion: 0,
          season: 1,
          tournament: null,
        },
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    expect(() => loadAllGames()).toThrow(/unsupported game export version 8/);
  });

  it("loads current v9 exports", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json5": JSON.stringify(
        makeV9Export({
          players: [
            {
              name: "Alice",
              type: "pilot",
              deck_name: "Azorius Control",
              tool_calls_ok: 3,
              tool_calls_failed: 1,
              thinking_time_secs: 12.5,
            },
            {
              name: "Bob",
              type: "pilot",
              commander: "Omnath, Locus of Creation",
              tool_calls_ok: 4,
              tool_calls_failed: 0,
              thinking_time_secs: 9.5,
            },
          ],
          annotations: [
            {
              decision_index: 0,
              snapshot_index: 1,
              player: "Alice",
              type: "blunder",
              severity: "major",
              description: "Missed lethal",
              action_taken: "Passed",
              better_line: "Attack",
            },
            {
              decision_index: 1,
              snapshot_index: 2,
              player: "Bob",
              type: "blunder",
              severity: "minor",
              description: "Tapped land suboptimally",
              action_taken: "Cast spell",
              better_line: "Use different land",
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
    expect(games[0].players[0].tool_calls_ok).toBe(3);
    expect(games[0].players[0].thinking_time_secs).toBe(12.5);
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
        makeV9Export({
          total_turns: 4,
          players: [
            {
              name: "Alice",
              type: "pilot",
              tool_calls_ok: 3,
              tool_calls_failed: 1,
              thinking_time_secs: 12.5,
            },
            {
              name: "Bob",
              type: "pilot",
              tool_calls_ok: 4,
              tool_calls_failed: 0,
              thinking_time_secs: 9.5,
            },
          ],
          annotations: [
            {
              decision_index: 0,
              snapshot_index: 1,
              player: "Alice",
              type: "blunder",
              severity: "questionable",
              description: "Low-confidence nit",
              action_taken: "Passed",
              better_line: "Hold priority",
            },
            {
              decision_index: 1,
              snapshot_index: 2,
              player: "Bob",
              type: "blunder",
              severity: "moderate",
              description: "Missed interaction",
              action_taken: "Cast spell",
              better_line: "Use removal first",
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
        makeV9Export({
          players: [{ name: "Alice", type: "pilot" }],
        }),
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    expect(() => loadAllGames()).toThrow(/missing tool_calls_ok/);
  });

  it("rejects exports with invalid blunder severities", async () => {
    clearGamesCache();
    mockGameFiles({
      "game_20260301_120000.json5": JSON.stringify(
        makeV9Export({
          annotations: [
            {
              decision_index: 0,
              snapshot_index: 1,
              player: "Alice",
              type: "blunder",
              severity: "catastrophic",
              description: "Unexpected severity",
              action_taken: "Passed",
              better_line: "Attack",
            },
          ],
        }),
      ),
    });

    const { loadAllGames } = await import("../src/utils/load-games.ts");
    expect(() => loadAllGames()).toThrow(/invalid severity/);
  });
});
