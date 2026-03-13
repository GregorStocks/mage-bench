const DEFAULT_IMPORTERS = {
  replay: async function () {
    return (await import("./init-game-replay.js")).initGameReplayPage;
  },
  golden: async function () {
    return (await import("./init-golden-viewer.js")).initGoldenViewerPage;
  },
  live: async function () {
    return (await import("./init-live-game.js")).initLiveGamePage;
  },
};

export async function initSpectatorPage(options) {
  var root = options && options.root ? options.root : document.querySelector("[data-spectator-mode]");
  if (!root) {
    return;
  }

  var mode = root.dataset.spectatorMode;
  if (!mode) {
    throw new Error("Missing data-spectator-mode");
  }

  var importers = options && options.importers ? options.importers : DEFAULT_IMPORTERS;
  var loadInitializer = importers[mode];
  if (typeof loadInitializer !== "function") {
    throw new Error("Unsupported spectator mode: " + mode);
  }

  var initializer = await loadInitializer();
  if (typeof initializer !== "function") {
    throw new Error("Missing initializer for spectator mode: " + mode);
  }

  return initializer({ root: root });
}
