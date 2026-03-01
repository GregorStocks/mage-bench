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

  test("leaderboard page", () => {
    const html = readPage("leaderboard");
    expect(html).toContain("Leaderboard");
    expect(html).toContain("leaderboard-table");
  });

  test("games index page", () => {
    const html = readPage("games");
    expect(html).toContain("Games");
    expect(html).toContain("Replay past mage-bench games");
  });

  test("architecture page", () => {
    const html = readPage("architecture");
    expect(html).toContain("Architecture");
    expect(html).toContain("XMage");
  });

  test("methodology page", () => {
    const html = readPage("methodology");
    expect(html).toContain("Methodology");
    expect(html).toContain("Ratings");
  });

  test("mcp-tools page", () => {
    const html = readPage("mcp-tools");
    expect(html).toContain("MCP Tools");
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

describe("leaderboard has data", () => {
  test("leaderboard table has at least one model row", () => {
    const html = readPage("leaderboard");
    // Each model row has a data-model-id attribute
    const modelRows = html.match(/data-model-id=/g);
    expect(modelRows).not.toBeNull();
    expect(modelRows.length).toBeGreaterThan(0);
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
    expect(html).toContain("game-viewer.js");
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

  test("games index embeds game data for client-side rendering", () => {
    const html = readPage("games");
    // Astro define:vars creates `const games = [...]` then `var __games = games`
    const match = html.match(/const games = (\[.*?\]);/s);
    expect(match).not.toBeNull();
    const games = JSON.parse(match[1]);
    expect(games.length).toBeGreaterThan(0);
  });
});
