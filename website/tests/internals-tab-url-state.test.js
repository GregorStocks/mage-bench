import { describe, expect, it } from "vitest";

import {
  isInternalsTabName,
  syncInternalsTabToUrl,
} from "../src/utils/internals-tab-url-state";

describe("isInternalsTabName", () => {
  it("accepts known internals tabs", () => {
    expect(isInternalsTabName("trends")).toBe(true);
    expect(isInternalsTabName("model-stats")).toBe(true);
    expect(isInternalsTabName("blunder")).toBe(true);
    expect(isInternalsTabName("golden")).toBe(true);
  });

  it("rejects unknown tab names", () => {
    expect(isInternalsTabName("leaderboard")).toBe(false);
  });
});

describe("syncInternalsTabToUrl", () => {
  it("keeps only trends params for the default trends tab", () => {
    const url = new URL(
      "https://example.test/internals?tab=model-stats&sort=gamesPlayed&dir=asc&statsModel=gpt&bmetric=cost&metric=timeoutRate"
    );
    syncInternalsTabToUrl(url, "trends");
    expect(url.searchParams.get("tab")).toBeNull();
    expect(url.searchParams.get("metric")).toBe("timeoutRate");
    expect(url.searchParams.get("sort")).toBeNull();
    expect(url.searchParams.get("statsModel")).toBeNull();
    expect(url.searchParams.get("bmetric")).toBeNull();
  });

  it("keeps only model-stats params for the model-stats tab", () => {
    const url = new URL(
      "https://example.test/internals?metric=timeoutRate&models=none&sort=gamesPlayed&dir=asc&statsModel=gpt&bmetric=cost"
    );
    syncInternalsTabToUrl(url, "model-stats");
    expect(url.searchParams.get("tab")).toBe("model-stats");
    expect(url.searchParams.get("sort")).toBe("gamesPlayed");
    expect(url.searchParams.get("dir")).toBe("asc");
    expect(url.searchParams.get("statsModel")).toBe("gpt");
    expect(url.searchParams.get("metric")).toBeNull();
    expect(url.searchParams.get("models")).toBeNull();
    expect(url.searchParams.get("bmetric")).toBeNull();
  });

  it("clears all tab-specific params for the golden tab", () => {
    const url = new URL(
      "https://example.test/internals?metric=timeoutRate&models=none&sort=gamesPlayed&bmetric=cost"
    );
    syncInternalsTabToUrl(url, "golden");
    expect(url.searchParams.get("tab")).toBe("golden");
    expect(url.searchParams.get("metric")).toBeNull();
    expect(url.searchParams.get("models")).toBeNull();
    expect(url.searchParams.get("sort")).toBeNull();
    expect(url.searchParams.get("bmetric")).toBeNull();
  });

  it("preserves unrelated params", () => {
    const url = new URL(
      "https://example.test/internals?foo=bar&metric=timeoutRate&bmetric=cost"
    );
    syncInternalsTabToUrl(url, "trends");
    expect(url.searchParams.get("foo")).toBe("bar");
    expect(url.searchParams.get("metric")).toBe("timeoutRate");
    expect(url.searchParams.get("bmetric")).toBeNull();
  });
});
