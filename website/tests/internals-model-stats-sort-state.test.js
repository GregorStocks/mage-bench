import { describe, expect, it } from "vitest";

import {
  defaultInternalsModelStatsSortState,
  parseInternalsModelStatsSortState,
  syncInternalsModelStatsSortStateToUrl,
} from "../src/utils/internals-model-stats-sort-state";

const sortTypes = {
  modelName: "string",
  provider: "string",
  gamesPlayed: "number",
  timeoutRate: "number",
  totalTimeouts: "number",
  totalOtherErrors: "number",
  contextResets: "number",
  avgPromptTokens: "number",
  avgCompletionTokens: "number",
  avgCost: "number",
  cacheRate: "number",
  reasoningPct: "number",
  latencyP50: "number",
  latencyP95: "number",
};

describe("defaultInternalsModelStatsSortState", () => {
  it("uses timeout rate desc by default", () => {
    expect(defaultInternalsModelStatsSortState()).toEqual({
      column: "timeoutRate",
      ascending: false,
    });
  });
});

describe("parseInternalsModelStatsSortState", () => {
  it("reads an explicit sort and direction from the URL", () => {
    const params = new URLSearchParams("sort=gamesPlayed&dir=asc");
    expect(parseInternalsModelStatsSortState(params, sortTypes)).toEqual({
      column: "gamesPlayed",
      ascending: true,
    });
  });

  it("falls back to the default sort when sort is missing", () => {
    const params = new URLSearchParams("");
    expect(parseInternalsModelStatsSortState(params, sortTypes)).toEqual({
      column: "timeoutRate",
      ascending: false,
    });
  });

  it("rejects invalid sort columns", () => {
    const params = new URLSearchParams("sort=rating&dir=desc");
    expect(parseInternalsModelStatsSortState(params, sortTypes)).toEqual({
      column: "timeoutRate",
      ascending: false,
    });
  });

  it("infers ascending for string columns when dir is omitted", () => {
    const params = new URLSearchParams("sort=modelName");
    expect(parseInternalsModelStatsSortState(params, sortTypes)).toEqual({
      column: "modelName",
      ascending: true,
    });
  });

  it("infers descending for numeric columns when dir is omitted", () => {
    const params = new URLSearchParams("sort=avgCost");
    expect(parseInternalsModelStatsSortState(params, sortTypes)).toEqual({
      column: "avgCost",
      ascending: false,
    });
  });
});

describe("syncInternalsModelStatsSortStateToUrl", () => {
  it("omits query params for the default sort", () => {
    const url = new URL("https://example.test/internals?sort=timeoutRate&dir=desc");
    syncInternalsModelStatsSortStateToUrl(url, {
      column: "timeoutRate",
      ascending: false,
    });
    expect(url.search).toBe("");
  });

  it("writes non-default sorts into the URL", () => {
    const url = new URL("https://example.test/internals");
    syncInternalsModelStatsSortStateToUrl(url, {
      column: "gamesPlayed",
      ascending: true,
    });
    expect(url.searchParams.get("sort")).toBe("gamesPlayed");
    expect(url.searchParams.get("dir")).toBe("asc");
  });
});
