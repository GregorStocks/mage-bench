import { describe, expect, it } from "vitest";

import {
  defaultRankingsSortState,
  parseRankingsSortState,
  syncRankingsSortStateToUrl,
} from "../src/utils/rankings-sort-state";

const sortTypes = {
  modelName: "string",
  provider: "string",
  rating: "number",
  blunderScore: "number",
  gamesPlayed: "number",
  winRate: "number",
  avgApiCost: "number",
};

describe("defaultRankingsSortState", () => {
  it("uses rating desc for rated leaderboards", () => {
    expect(defaultRankingsSortState(false)).toEqual({
      column: "rating",
      ascending: false,
    });
  });

  it("uses win rate desc for exhibition leaderboards", () => {
    expect(defaultRankingsSortState(true)).toEqual({
      column: "winRate",
      ascending: false,
    });
  });
});

describe("parseRankingsSortState", () => {
  it("reads an explicit sort and direction from the URL", () => {
    const params = new URLSearchParams("sort=gamesPlayed&dir=asc");
    expect(parseRankingsSortState(params, false, sortTypes)).toEqual({
      column: "gamesPlayed",
      ascending: true,
    });
  });

  it("falls back to the active format default when the sort is missing", () => {
    const params = new URLSearchParams("");
    expect(parseRankingsSortState(params, false, sortTypes)).toEqual({
      column: "rating",
      ascending: false,
    });
  });

  it("rejects rating sorts for exhibition formats", () => {
    const params = new URLSearchParams("sort=rating&dir=desc");
    expect(parseRankingsSortState(params, true, sortTypes)).toEqual({
      column: "winRate",
      ascending: false,
    });
  });

  it("infers ascending for string columns when dir is omitted", () => {
    const params = new URLSearchParams("sort=modelName");
    expect(parseRankingsSortState(params, false, sortTypes)).toEqual({
      column: "modelName",
      ascending: true,
    });
  });

  it("infers descending for numeric columns when dir is omitted", () => {
    const params = new URLSearchParams("sort=avgApiCost");
    expect(parseRankingsSortState(params, false, sortTypes)).toEqual({
      column: "avgApiCost",
      ascending: false,
    });
  });
});

describe("syncRankingsSortStateToUrl", () => {
  it("omits query params for the default rated sort", () => {
    const url = new URL("https://example.test/season/1/rankings?sort=rating&dir=desc");
    syncRankingsSortStateToUrl(url, { column: "rating", ascending: false }, false);
    expect(url.search).toBe("");
  });

  it("omits query params for the default exhibition sort", () => {
    const url = new URL("https://example.test/season/1/rankings?sort=winRate&dir=desc");
    syncRankingsSortStateToUrl(url, { column: "winRate", ascending: false }, true);
    expect(url.search).toBe("");
  });

  it("writes non-default sorts into the URL", () => {
    const url = new URL("https://example.test/season/1/rankings");
    syncRankingsSortStateToUrl(url, { column: "gamesPlayed", ascending: true }, false);
    expect(url.searchParams.get("sort")).toBe("gamesPlayed");
    expect(url.searchParams.get("dir")).toBe("asc");
  });
});
