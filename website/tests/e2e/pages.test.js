import { test, expect, describe } from "vitest";
import fs from "node:fs";
import path from "node:path";

const distDir = path.join(process.cwd(), "dist");

function readPage(pagePath) {
  // Root page is index.html, subpages are pagePath/index.html
  const filePath = pagePath === "/"
    ? path.join(distDir, "index.html")
    : path.join(distDir, pagePath, "index.html");
  return fs.readFileSync(filePath, "utf-8");
}

describe("static build exists", () => {
  test("dist directory exists", () => {
    expect(fs.existsSync(distDir)).toBe(true);
  });
});

describe("top-level pages load with expected content", () => {
  test("home page", () => {
    const html = readPage("/");
    expect(html).toContain("mage-bench");
    expect(html).toContain("LLMs play Magic");
  });

  test("season rankings page", () => {
    const html = readPage("season/1/rankings");
    expect(html).toContain("Leaderboard");
    expect(html).toContain("leaderboard-table");
  });

  test("games index page", () => {
    const html = readPage("games");
    expect(html).toContain("Games");
    expect(html).toContain("Replay past mage-bench games");
  });

  test("scoring page", () => {
    const html = readPage("scoring");
    expect(html).toContain("Scoring");
    expect(html).toContain("Ratings");
  });

  test("contact page", () => {
    const html = readPage("contact");
    expect(html).toContain("Contact");
  });

  test("internals page", () => {
    const html = readPage("internals");
    expect(html).toContain("Internals");
  });
});

describe("season rankings has data", () => {
  test("rankings table has at least one model row", () => {
    const html = readPage("season/1/rankings");
    // Each model row has a data-model-id attribute
    const modelRows = html.match(/data-model-id=/g);
    expect(modelRows).not.toBeNull();
    expect(modelRows.length).toBeGreaterThan(0);
  });
});

describe("benchmark-results excluded games count is consistent", () => {
  test("excludedGames matches actual count of games below minEpoch", () => {
    const benchmarkPath = path.join(process.cwd(), "src", "data", "benchmark-results.json");
    const data = JSON.parse(fs.readFileSync(benchmarkPath, "utf-8"));
    const { excludedGames, minEpoch, epochCounts } = data;
    if (!minEpoch || !epochCounts) return; // skip if fields missing
    const countBelow = Object.entries(epochCounts)
      .filter(([epoch]) => parseInt(epoch) < minEpoch)
      .reduce((sum, [, count]) => sum + count, 0);
    expect(excludedGames).toBe(countBelow);
  });
});

describe("game pages", () => {
  test("at least one game page exists", () => {
    const gamesDir = path.join(distDir, "games");
    const entries = fs.readdirSync(gamesDir, { withFileTypes: true });
    const gameDirs = entries.filter(
      (e) => e.isDirectory() && e.name.startsWith("game_")
    );
    expect(gameDirs.length).toBeGreaterThan(0);
  });

  test("first game page has visualizer shell", () => {
    const gamesDir = path.join(distDir, "games");
    const entries = fs.readdirSync(gamesDir, { withFileTypes: true });
    const gameDirs = entries
      .filter((e) => e.isDirectory() && e.name.startsWith("game_"))
      .sort((a, b) => a.name.localeCompare(b.name));
    const firstGame = gameDirs[0].name;
    const html = readPage(`games/${firstGame}`);
    expect(html).toContain('id="visualizer"');
    expect(html).toContain('id="viewer-container"');
    expect(html).toContain('id="game-replay-config"');
    expect(html).toContain("/_astro/");
    expect(html).toContain("Game Replay");
  });

  test("first game JSON has turns", () => {
    const publicGamesDir = path.join(process.cwd(), "public", "games");
    const gameFiles = fs
      .readdirSync(publicGamesDir)
      .filter((f) => f.startsWith("game_") && f.endsWith(".json"))
      .sort();
    expect(gameFiles.length).toBeGreaterThan(0);
    const data = JSON.parse(
      fs.readFileSync(path.join(publicGamesDir, gameFiles[0]), "utf-8")
    );
    expect(data.totalTurns).toBeGreaterThan(0);
    expect(data.snapshots.length).toBeGreaterThan(0);
  });

  test("games index server-renders game cards", () => {
    const html = readPage("games");
    const gameCards = html.match(/class="game-card"/g);
    expect(gameCards).not.toBeNull();
    expect(gameCards.length).toBeGreaterThan(0);
    expect(html).not.toContain("Loading games...");
  });
});
