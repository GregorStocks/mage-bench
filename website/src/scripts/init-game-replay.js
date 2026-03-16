import "./game-renderer.js";
import "./game-viewer.js";
import { getRequiredElement } from "./spectator-runtime.js";

function readConfig() {
  var configEl = document.getElementById("game-replay-config");
  if (!configEl) {
    throw new Error("Missing #game-replay-config script");
  }
  return JSON.parse(configEl.textContent || "null");
}

function readInlineGameData() {
  var el = document.getElementById("game-data");
  if (!el) return null;
  var text = el.textContent || "";
  if (!text.trim()) return null;
  return JSON.parse(text);
}

function readRunningCosts() {
  var el = document.getElementById("running-costs");
  if (!el) return null;
  var text = el.textContent || "";
  if (!text.trim()) return null;
  return JSON.parse(text);
}

export async function initGameReplayPage(options) {
  var visualizer = options && options.root ? options.root : document.getElementById("visualizer");
  if (!visualizer) {
    return;
  }

  var { auditMode } = readConfig();
  if (auditMode) {
    await import("./audit-panel.js");
  }

  var gameViewer = window.GameViewer;
  if (!gameViewer) {
    throw new Error("game-viewer.js did not initialize window.GameViewer");
  }

  var slug = window.location.pathname.replace(/^\/games\//, "").replace(/\/$/, "");
  var errorEl = getRequiredElement(visualizer, "#error");
  var viewerContainer = getRequiredElement(visualizer, "#viewer-container");
  if (!slug) {
    errorEl.textContent = "No game ID in URL.";
    errorEl.classList.remove("hidden");
    return;
  }

  // Use inline game data (prerendered at build time) or fall back to fetch
  var gameDataPromise;
  var inlineGame = readInlineGameData();
  if (inlineGame) {
    gameDataPromise = Promise.resolve(inlineGame);
  } else {
    gameDataPromise = gameViewer.fetchGameData("", slug);
  }

  gameDataPromise
    .then(function (game) {
      if (!game.snapshots || game.snapshots.length === 0) {
        throw new Error("No snapshots in game data.");
      }

      var params = new URLSearchParams(window.location.search);
      var startSnap = params.get("s");
      var initialSnapshot = 0;
      if (startSnap !== null) {
        var snapNum = parseInt(startSnap, 10);
        if (!isNaN(snapNum) && snapNum >= 0 && snapNum < game.snapshots.length) {
          initialSnapshot = snapNum;
        }
      }

      var initialDecision = null;
      if (auditMode) {
        var startDec = params.get("d");
        if (startDec !== null) {
          initialDecision = parseInt(startDec, 10);
          if (isNaN(initialDecision)) initialDecision = null;
        }
      }

      var viewer = gameViewer.create(viewerContainer, game, {
        initialSnapshot: initialSnapshot,
        runningCostBySnapshot: readRunningCosts(),
        onSnapshotChange: function (index) {
          var url = new URL(window.location);
          if (index === 0) {
            url.searchParams.delete("s");
          } else {
            url.searchParams.set("s", index);
          }
          history.replaceState(null, "", url);
        },
      });

      if (auditMode) {
        var auditPanel = window.AuditPanel;
        if (!auditPanel) {
          throw new Error("audit-panel.js did not initialize window.AuditPanel");
        }
        var auditContainer = getRequiredElement(visualizer, "#audit-container");
        auditPanel.create(auditContainer, viewer, slug, {
          initialDecision: initialDecision,
          onDecisionChange: function (di) {
            var url = new URL(window.location);
            if (di != null) {
              url.searchParams.set("d", di);
            } else {
              url.searchParams.delete("d");
            }
            history.replaceState(null, "", url);
          },
        });
      }

      viewerContainer.focus({ preventScroll: true });
    })
    .catch(function (err) {
      errorEl.textContent = err.message || "Failed to load game data.";
      errorEl.classList.remove("hidden");
    });
}
