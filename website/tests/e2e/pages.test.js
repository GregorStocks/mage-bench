import { test, expect, describe } from "vitest";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { parseJSON5 } from "../../src/utils/parse-json5.ts";
import { normalizeGameExport } from "../../src/utils/normalize-game-export.ts";
import { loadLatestCompletedTournament } from "../../src/utils/season-data.ts";
import {
  buildReplayTitle,
  formatReplayBlunderSummary,
  summarizeReplayBlunders,
} from "../../src/utils/replay-metadata.ts";

const distDir = path.join(process.cwd(), "dist");

function readPage(pagePath) {
  // Root page is index.html, subpages are pagePath/index.html
  const filePath = pagePath === "/"
    ? path.join(distDir, "index.html")
    : path.join(distDir, pagePath, "index.html");
  return fs.readFileSync(filePath, "utf-8");
}

function readBuiltFile(relativePath) {
  return fs.readFileSync(path.join(distDir, relativePath), "utf-8");
}

function readBuiltJson(relativePath) {
  return JSON.parse(readBuiltFile(relativePath));
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function readGameExport(slug) {
  const publicGamesDir = path.join(process.cwd(), "public", "games");
  const json5Path = path.join(publicGamesDir, `${slug}.json5`);
  if (fs.existsSync(json5Path)) {
    return normalizeGameExport(parseJSON5(fs.readFileSync(json5Path, "utf-8")));
  }

  const gzPath = path.join(publicGamesDir, `${slug}.json5.gz`);
  if (fs.existsSync(gzPath)) {
    return normalizeGameExport(parseJSON5(zlib.gunzipSync(fs.readFileSync(gzPath)).toString("utf-8")));
  }

  throw new Error(`Missing game export for ${slug}`);
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

  test("home page championship banner follows current season data", () => {
    const html = readPage("/");
    const championship = loadLatestCompletedTournament();

    if (championship == null) {
      expect(html).not.toContain('class="champ-banner"');
      return;
    }

    expect(html).toContain(`/season/${championship.season}/results`);
    expect(html).toContain(`Season ${championship.season} Champion`);
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
    expect(html).toContain('data-data-url="/internals/data/trends.json"');
    expect(html).toContain('data-data-url="/internals/data/model-stats.json"');
    expect(html).toContain('data-data-url="/internals/data/blunder.json"');
    expect(html).not.toContain('id="model-stats-data"');
    expect(html).not.toContain('id="internals-data"');
    expect(html).not.toContain('id="blunder-data"');
  });
});

describe("internals data endpoints", () => {
  test("internals dashboard JSON endpoints are prerendered", () => {
    const trendData = readBuiltJson("internals/data/trends.json");
    const modelStatsData = readBuiltJson("internals/data/model-stats.json");
    const blunderData = readBuiltJson("internals/data/blunder.json");

    expect(Array.isArray(trendData.games)).toBe(true);
    expect(typeof modelStatsData.models).toBe("object");
    expect(Array.isArray(blunderData.runs)).toBe(true);
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
    expect(html).toContain('data-spectator-mode="replay"');
    expect(html).toContain('id="viewer-container"');
    expect(html).toContain('id="game-replay-config"');
    expect(html).toContain("/_astro/");
    expect(html).not.toContain('<div id="game-title"></div>');
  });

  test("first game page server-renders replay metadata", () => {
    const gamesDir = path.join(distDir, "games");
    const entries = fs.readdirSync(gamesDir, { withFileTypes: true });
    const gameDirs = entries
      .filter((e) => e.isDirectory() && e.name.startsWith("game_"))
      .sort((a, b) => a.name.localeCompare(b.name));
    const firstGame = gameDirs[0].name;
    const html = readPage(`games/${firstGame}`);
    const game = readGameExport(firstGame);
    const replayTitle = buildReplayTitle(game.players);
    const escapedReplayTitle = escapeHtml(replayTitle);

    expect(html).toContain(`<title>${escapedReplayTitle} | mage-bench</title>`);
    expect(html).toContain(escapedReplayTitle);
    expect(html).toContain(`Season ${game.season}`);

    if (game.youtube_url) {
      expect(html).toContain("Watch on YouTube");
    }

    const blunderSummary = summarizeReplayBlunders(game.annotations);
    if (blunderSummary != null) {
      expect(html).toContain(formatReplayBlunderSummary(blunderSummary));
    }

    if (game.errors && game.errors.length > 0) {
      expect(html).toContain(
        `${game.errors.length} critical error${game.errors.length === 1 ? "" : "s"}`,
      );
    }

    if (game.season === 0) {
      expect(html).toContain("This is a Season 0 game.");
    }
  });

  test("first game JSON has turns", () => {
    const publicGamesDir = path.join(process.cwd(), "public", "games");
    const gameFiles = fs
      .readdirSync(publicGamesDir)
      .filter((f) => f.startsWith("game_") && f.endsWith(".json5"))
      .sort();
    expect(gameFiles.length).toBeGreaterThan(0);
    const data = normalizeGameExport(parseJSON5(
      fs.readFileSync(path.join(publicGamesDir, gameFiles[0]), "utf-8")
    ));
    expect(data.total_turns).toBeGreaterThan(0);
    expect(data.snapshots.length).toBeGreaterThan(0);
  });

  test("games index server-renders game cards", () => {
    const html = readPage("games");
    const gameCards = html.match(/class="game-card surface-card"/g);
    expect(gameCards).not.toBeNull();
    expect(gameCards.length).toBeGreaterThan(0);
    expect(html).not.toContain("Loading games...");
  });

  test("season results server-renders tournament game cards", () => {
    const html = readPage("season/1/results");
    expect(html).toContain("Tournament Games");
    expect(html).toContain('id="tournament-games-list"');
    expect(html).toContain("Tournament</span>");
    expect(html).toContain('class="game-card surface-card"');
  });
});
