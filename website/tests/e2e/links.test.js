import { test, expect, describe, beforeAll } from "vitest";
import fs from "node:fs";
import path from "node:path";

const distDir = path.join(process.cwd(), "dist");

function stripTrailingSlash(href) {
  return href.endsWith("/") && href !== "/" ? href.slice(0, -1) : href;
}

/**
 * Single walk of dist/ that collects both:
 * - links: Map of href -> Set<sourcePage> (every internal href found in HTML)
 * - pages: Set of page paths (every directory containing index.html)
 */
function crawlDist() {
  const links = new Map(); // href -> Set<sourcePage>
  const pages = new Set(); // page paths like "/", "/leaderboard"

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.name === "index.html") {
        // Record as a page
        const rel = path.relative(distDir, path.dirname(full));
        pages.add(rel === "" ? "/" : "/" + rel);

        // Also extract links (index.html is HTML)
        const html = fs.readFileSync(full, "utf-8");
        const sourcePage = "/" + path.relative(distDir, full);
        extractLinks(html, sourcePage, links);
      } else if (entry.name.endsWith(".html")) {
        const html = fs.readFileSync(full, "utf-8");
        const sourcePage = "/" + path.relative(distDir, full);
        extractLinks(html, sourcePage, links);
      }
    }
  }

  walk(distDir);
  return { links, pages };
}

function extractLinks(html, sourcePage, links) {
  for (const match of html.matchAll(/href=["']([^"']*?)["']/g)) {
    const raw = match[1];
    if (raw === "" || raw.startsWith("#")) {
      continue;
    }
    // Strip query string and fragment
    const href = raw.split("?")[0].split("#")[0];
    // Only track internal root-relative links. Skip schemes, relative paths, and protocol-relative hrefs.
    if (!href.startsWith("/") || href.startsWith("//")) continue;
    if (!links.has(href)) links.set(href, new Set());
    links.get(href).add(sourcePage);
  }
}

/**
 * Check if an internal href resolves to a file in dist/.
 * Astro generates: /foo -> dist/foo/index.html
 * Static assets: /favicon.svg -> dist/favicon.svg
 */
function linkExists(href) {
  const cleaned = stripTrailingSlash(href);
  const rel = cleaned === "/" ? "" : cleaned;

  // Check as directory with index.html (Astro page)
  const asPage = path.join(distDir, rel, "index.html");
  if (fs.existsSync(asPage)) return true;

  // Check as direct file (static asset like /favicon.svg, /game-viewer.css)
  const asFile = path.join(distDir, rel);
  if (fs.existsSync(asFile) && fs.statSync(asFile).isFile()) return true;

  return false;
}

describe("internal links", () => {
  let links;
  let pages;

  beforeAll(() => {
    const result = crawlDist();
    links = result.links;
    pages = result.pages;
  });

  test("dist directory exists", () => {
    expect(fs.existsSync(distDir)).toBe(true);
  });

  test("extractLinks keeps only internal root-relative links", () => {
    const links = new Map();

    extractLinks(
      [
        '<a href="javascript:alert(1)">js</a>',
        '<a href="data:text/html,boom">data</a>',
        '<a href="vbscript:msgbox(1)">vb</a>',
        '<a href="https://example.com">ext</a>',
        '<a href="//example.com/path">protocol-relative</a>',
        '<a href="leaderboard">relative</a>',
        '<a href="/leaderboard">ok</a>',
      ].join(""),
      "/index.html",
      links
    );

    expect([...links.keys()]).toEqual(["/leaderboard"]);
  });

  test("all internal links resolve to existing pages or files", () => {
    const broken = [];

    for (const [href, sources] of links) {
      if (!linkExists(href)) {
        broken.push({ href, sources: [...sources].slice(0, 3) });
      }
    }

    if (broken.length > 0) {
      const report = broken
        .map(
          (b) =>
            `  ${b.href}\n    linked from: ${b.sources.join(", ")}${b.sources.length < broken.find((x) => x.href === b.href).sources.size ? " ..." : ""}`
        )
        .join("\n");
      expect.fail(
        `Found ${broken.length} broken internal link(s):\n${report}`
      );
    }
  });

  test("all pages are reachable from at least one other page", () => {
    const linkedTo = new Set();

    for (const href of links.keys()) {
      linkedTo.add(stripTrailingSlash(href));
    }

    const unreachable = [];
    for (const page of pages) {
      if (page === "/") continue; // homepage is always an entry point
      if (/^\/games\/game_/.test(page)) continue; // game pages linked dynamically via JS
      if (page === "/games/live") continue; // live viewer accessed via direct URL
      if (!linkedTo.has(page)) {
        unreachable.push(page);
      }
    }

    if (unreachable.length > 0) {
      expect.fail(
        `Found ${unreachable.length} unreachable page(s) (not linked from anywhere):\n` +
          unreachable.map((p) => `  ${p}`).join("\n")
      );
    }
  });

  test("found a reasonable number of internal links", () => {
    // Sanity check: the site should have many internal links
    expect(links.size).toBeGreaterThan(10);
  });
});
