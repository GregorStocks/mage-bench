import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { loadGoldenTestScenarios } from "../src/utils/load-internals-dashboard-data.ts";

describe("loadGoldenTestScenarios", () => {
  it("loads sorted scenario metadata from golden exports", () => {
    const scenarios = loadGoldenTestScenarios();

    expect(scenarios.length).toBeGreaterThan(0);
    expect(scenarios.map((scenario) => scenario.name)).toEqual(
      [...scenarios.map((scenario) => scenario.name)].sort()
    );
    expect(scenarios[0].snapshots).toBeGreaterThanOrEqual(0);
    expect(scenarios[0].turns).toBeGreaterThanOrEqual(0);
  });

  it("fails fast when the golden export directory is missing", () => {
    const missingDir = path.join(os.tmpdir(), `missing-golden-exports-${Date.now()}`);
    expect(() => loadGoldenTestScenarios(missingDir)).toThrow(
      /Missing golden exports directory/
    );
  });
});
