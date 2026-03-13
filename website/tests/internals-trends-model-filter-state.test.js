import { describe, expect, it } from "vitest";

import {
  parseInternalsTrendsModelFilterState,
  syncInternalsTrendsModelFilterStateToUrl,
} from "../src/utils/internals-trends-model-filter-state";

const allModelKeys = [
  "anthropic/claude-sonnet-4",
  "openai/gpt-5",
  "x-ai/grok-4.1-fast",
];

describe("parseInternalsTrendsModelFilterState", () => {
  it("defaults to all models with an empty search", () => {
    const params = new URLSearchParams("");
    expect(parseInternalsTrendsModelFilterState(params, allModelKeys)).toEqual({
      selectedModelKeys: allModelKeys,
      search: "",
    });
  });

  it("reads a selected subset and search text from the URL", () => {
    const params = new URLSearchParams(
      "models=openai/gpt-5,x-ai/grok-4.1-fast&modelSearch=grok"
    );
    expect(parseInternalsTrendsModelFilterState(params, allModelKeys)).toEqual({
      selectedModelKeys: ["openai/gpt-5", "x-ai/grok-4.1-fast"],
      search: "grok",
    });
  });

  it("supports explicitly selecting no models", () => {
    const params = new URLSearchParams("models=none");
    expect(parseInternalsTrendsModelFilterState(params, allModelKeys)).toEqual({
      selectedModelKeys: [],
      search: "",
    });
  });

  it("drops invalid model keys and falls back to the default when none remain", () => {
    const params = new URLSearchParams("models=missing-model");
    expect(parseInternalsTrendsModelFilterState(params, allModelKeys)).toEqual({
      selectedModelKeys: allModelKeys,
      search: "",
    });
  });
});

describe("syncInternalsTrendsModelFilterStateToUrl", () => {
  it("omits params for the default all-model empty-search state", () => {
    const url = new URL("https://example.test/internals?models=none&modelSearch=gpt");
    syncInternalsTrendsModelFilterStateToUrl(url, {
      selectedModelKeys: allModelKeys,
      search: "",
    }, allModelKeys);
    expect(url.search).toBe("");
  });

  it("writes a canonical selected subset", () => {
    const url = new URL("https://example.test/internals");
    syncInternalsTrendsModelFilterStateToUrl(url, {
      selectedModelKeys: ["x-ai/grok-4.1-fast", "openai/gpt-5"],
      search: "",
    }, allModelKeys);
    expect(url.searchParams.get("models")).toBe(
      "openai/gpt-5,x-ai/grok-4.1-fast"
    );
    expect(url.searchParams.has("modelSearch")).toBe(false);
  });

  it("writes the explicit none state and search text", () => {
    const url = new URL("https://example.test/internals");
    syncInternalsTrendsModelFilterStateToUrl(url, {
      selectedModelKeys: [],
      search: "claude",
    }, allModelKeys);
    expect(url.searchParams.get("models")).toBe("none");
    expect(url.searchParams.get("modelSearch")).toBe("claude");
  });
});
