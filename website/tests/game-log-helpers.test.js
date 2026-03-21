import { describe, expect, it } from "vitest";

import { decodeHtmlEntitiesOnce } from "../src/scripts/game-log-helpers.ts";

describe("decodeHtmlEntitiesOnce", () => {
  it("decodes a single layer of named entities", () => {
    expect(decodeHtmlEntitiesOnce("Good luck &lt;friend&gt; &amp; have fun"))
      .toBe("Good luck <friend> & have fun");
  });

  it("does not double-decode amp-escaped named entities", () => {
    expect(decodeHtmlEntitiesOnce("&amp;lt;")).toBe("&lt;");
  });

  it("decodes a single layer of numeric entities", () => {
    expect(decodeHtmlEntitiesOnce("&#60;")).toBe("<");
  });

  it("does not double-decode amp-escaped numeric entities", () => {
    expect(decodeHtmlEntitiesOnce("&amp;#60;")).toBe("&#60;");
  });
});
