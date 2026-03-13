import { beforeEach, describe, expect, it, vi } from "vitest";

await import("../src/scripts/game-list.js");

const GameList = window.GameList;

describe("GameList", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div data-game-list-root data-show-season-filter="1">
        <div id="season-filter"></div>
        <div id="format-tabs"></div>
        <div id="model-tabs"></div>
        <div id="filter-bar" hidden></div>
        <div id="games-list">
          <article
            class="game-card"
            data-game-id="game_test_123"
            data-format="jumpstart"
            data-season="1"
            data-model-entries='[{"model":"openai/gpt-5","effort":"medium"}]'
          >
            <a href="/games/game_test_123" class="game-card-main">
              <div class="game-players">
                <div class="player-cell" data-rating-key="openai/gpt-5::medium"></div>
              </div>
            </a>
          </article>
          <article
            class="game-card"
            data-game-id="game_test_456"
            data-format="commander"
            data-season="0"
            data-model-entries='[{"model":"anthropic/claude-opus","effort":""}]'
          >
            <a href="/games/game_test_456" class="game-card-main">
              <div class="game-players">
                <div class="player-cell" data-rating-key="anthropic/claude-opus"></div>
              </div>
            </a>
          </article>
        </div>
        <p id="games-empty-message" class="no-games" hidden>No games match the current filters.</p>
      </div>
    `;

    window.history.replaceState({}, "", "/games?format=commander");
    globalThis.fetch = vi.fn(async () => ({ ok: false }));
  });

  it("filters pre-rendered cards while preserving replay links", async () => {
    GameList.init({ showSeasonFilter: true });

    await vi.waitFor(() => {
      expect(document.querySelector("#format-tabs .format-tab.active")).not.toBeNull();
    });

    const cards = Array.from(document.querySelectorAll(".game-card"));
    const links = Array.from(document.querySelectorAll(".game-card-main"));
    expect(cards).toHaveLength(2);
    expect(links[0].getAttribute("href")).toBe("/games/game_test_123");
    expect(links[0].href).toBe("http://localhost:3000/games/game_test_123");
    expect(cards[0].hidden).toBe(true);
    expect(cards[1].hidden).toBe(false);
    expect(document.getElementById("filter-bar").hidden).toBe(false);
  });
});
