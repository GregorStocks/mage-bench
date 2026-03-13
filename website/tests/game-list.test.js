import { beforeEach, describe, expect, it, vi } from "vitest";

await import("../src/scripts/game-list.js");

const GameList = window.GameList;

describe("GameList", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="season-filter"></div>
      <div id="format-tabs"></div>
      <div id="model-tabs"></div>
      <div id="filter-bar"></div>
      <div id="games-list"></div>
    `;

    window.history.replaceState({}, "", "/games");
    globalThis.fetch = vi.fn(async () => ({ ok: false }));
  });

  it("renders clickable replay links for game cards", async () => {
    GameList.init({
      games: [
        {
          id: "game_test_123",
          timestamp: "20260101_120000",
          deckType: "Limited",
          totalTurns: 5,
          players: [
            { name: "Alice" },
            { name: "Bob" },
          ],
        },
      ],
      minBlunderVersion: 11,
      showSeasonFilter: true,
      showSeasonBadge: true,
    });

    await vi.waitFor(() => {
      expect(document.querySelector(".game-card")).not.toBeNull();
    });

    const link = document.querySelector(".game-card");
    expect(link.getAttribute("href")).toBe("/games/game_test_123");
    expect(link.href).toBe("http://localhost:3000/games/game_test_123");
  });
});
