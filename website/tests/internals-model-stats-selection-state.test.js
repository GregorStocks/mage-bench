import { describe, expect, it } from "vitest";

import {
  parseInternalsModelStatsSelectedModelKey,
  syncInternalsModelStatsSelectedModelKeyToUrl,
} from "../src/utils/internals-model-stats-selection-state";

const modelKeys = [
  "anthropic/claude-sonnet-4",
  "openai/gpt-5",
  "x-ai/grok-4.1-fast",
];

describe("parseInternalsModelStatsSelectedModelKey", () => {
  it("returns null when the URL has no selection", () => {
    const params = new URLSearchParams("");
    expect(parseInternalsModelStatsSelectedModelKey(params, modelKeys)).toBeNull();
  });

  it("returns the selected key when it is valid", () => {
    const params = new URLSearchParams("statsModel=openai/gpt-5");
    expect(parseInternalsModelStatsSelectedModelKey(params, modelKeys)).toBe(
      "openai/gpt-5"
    );
  });

  it("drops invalid selected model keys", () => {
    const params = new URLSearchParams("statsModel=missing-model");
    expect(parseInternalsModelStatsSelectedModelKey(params, modelKeys)).toBeNull();
  });
});

describe("syncInternalsModelStatsSelectedModelKeyToUrl", () => {
  it("removes the param when nothing is selected", () => {
    const url = new URL("https://example.test/internals?statsModel=openai/gpt-5");
    syncInternalsModelStatsSelectedModelKeyToUrl(url, null);
    expect(url.search).toBe("");
  });

  it("writes the selected key into the URL", () => {
    const url = new URL("https://example.test/internals");
    syncInternalsModelStatsSelectedModelKeyToUrl(url, "x-ai/grok-4.1-fast");
    expect(url.searchParams.get("statsModel")).toBe("x-ai/grok-4.1-fast");
  });
});
