import { beforeEach, describe, expect, it, vi } from "vitest";

const initSpectatorPageModule = await import("../src/scripts/init-spectator-page.js");
const initSpectatorPage = initSpectatorPageModule.initSpectatorPage;

describe("initSpectatorPage", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("returns when no spectator root is present", async () => {
    await expect(initSpectatorPage()).resolves.toBeUndefined();
  });

  it("dispatches to the configured mode initializer", async () => {
    document.body.innerHTML = '<div id="visualizer" data-spectator-mode="golden"></div>';
    const root = document.getElementById("visualizer");
    const initializer = vi.fn();

    await initSpectatorPage({
      root,
      importers: {
        golden: async () => initializer,
      },
    });

    expect(initializer).toHaveBeenCalledTimes(1);
    expect(initializer).toHaveBeenCalledWith({ root });
  });

  it("throws for unsupported spectator modes", async () => {
    document.body.innerHTML = '<div id="visualizer" data-spectator-mode="unknown"></div>';
    const root = document.getElementById("visualizer");

    await expect(initSpectatorPage({ root })).rejects.toThrow(
      "Unsupported spectator mode: unknown",
    );
  });
});
