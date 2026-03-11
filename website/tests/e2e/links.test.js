import { test, expect, describe } from "vitest";
import fs from "node:fs";
import path from "node:path";

const distDir = path.join(process.cwd(), "dist");

/**
 * Crawl all HTML files in dist/ and collect every internal href.
 * Returns a map of href -> list of pages that link to it.
 */
function collectInternalLinks() {
  const links = new Map(); // href -> Set<sourcePage>

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.name.endsWith(".html")) {
        const html = fs.readFileSync(full, "utf-8");
        const sourcePage = "/" + path.relative(distDir, full);
        // Match href="..." in all forms (single/double quotes)
        for (const match of html.matchAll(/href=["']([^"']*?)["']/g)) {
          const raw = match[1];
          // Skip external, protocol-relative, mailto, javascript, anchor-only
          if (
            raw.startsWith("http://") ||
            raw.startsWith("https://") ||
            raw.startsWith("//") ||
            raw.startsWith("mailto:") ||
            raw.startsWith("javascript:") ||
            raw.startsWith("#") ||
            raw === ""
          ) {
            continue;
          }
          // Strip query string and fragment
          const href = raw.split("?")[0].split("#")[0];
          if (!href.startsWith("/")) continue; // skip relative links for now
          if (!links.has(href)) links.set(href, new Set());
          links.get(href).add(sourcePage);
        }
      }
    }
  }

  walk(distDir);
  return links;
}

/**
 * Collect all HTML pages in dist/ (directories containing index.html).
 * Returns a set of page paths like "/", "/leaderboard", "/games", etc.
 */
function collectAllPages() {
  const pages = new Set();

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.name === "index.html") {
        const rel = path.relative(distDir, path.dirname(full));
        pages.add(rel === "" ? "/" : "/" + rel);
      }
    }
  }

  walk(distDir);
  return pages;
}

/**
 * Check if an internal href resolves to a file in dist/.
 * Astro generates: /foo -> dist/foo/index.html
 * Static assets: /favicon.svg -> dist/favicon.svg
 */
function linkExists(href) {
  // Normalize: remove trailing slash
  const cleaned = href.endsWith("/") && href !== "/" ? href.slice(0, -1) : href;
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
  test("dist directory exists", () => {
    expect(fs.existsSync(distDir)).toBe(true);
  });

  test("all internal links resolve to existing pages or files", () => {
    const links = collectInternalLinks();
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
    const allPages = collectAllPages();
    const links = collectInternalLinks();
    const linkedTo = new Set();

    for (const href of links.keys()) {
      // Normalize: strip trailing slash to match page format
      const cleaned =
        href.endsWith("/") && href !== "/" ? href.slice(0, -1) : href;
      linkedTo.add(cleaned);
    }

    const unreachable = [];
    for (const page of allPages) {
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
    const links = collectInternalLinks();
    // Sanity check: the site should have many internal links
    expect(links.size).toBeGreaterThan(10);
  });
});
