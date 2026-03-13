import "./game-renderer.js";
import "./game-viewer.js";

function readConfig() {
  const configEl = document.getElementById("game-replay-config");
  if (!configEl) {
    throw new Error("Missing #game-replay-config script");
  }
  return JSON.parse(configEl.textContent || "null");
}

function parseHttpUrl(rawUrl, fieldName) {
  const parsed = new URL(rawUrl, window.location.origin);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(fieldName + " must use http or https, got " + parsed.protocol);
  }
  return parsed.toString();
}

export async function initGameReplayPage() {
  const visualizer = document.getElementById("visualizer");
  if (!visualizer) {
    return;
  }

  const { minBlunderVersion, auditMode } = readConfig();
  if (auditMode) {
    await import("./audit-panel.js");
  }

  const gameViewer = window.GameViewer;
  if (!gameViewer) {
    throw new Error("game-viewer.js did not initialize window.GameViewer");
  }

  const slug = window.location.pathname.replace(/^\/games\//, "").replace(/\/$/, "");
  if (!slug) {
    document.getElementById("loading").classList.add("hidden");
    document.getElementById("error").textContent = "No game ID in URL.";
    document.getElementById("error").classList.remove("hidden");
    return;
  }

  const loadingEl = document.getElementById("loading");
  const errorEl = document.getElementById("error");
  const gameUI = document.getElementById("game-ui");
  const gameTitleEl = document.getElementById("game-title");
  const viewerContainer = document.getElementById("viewer-container");

  gameViewer.fetchGameData("", slug)
    .then(function (game) {
      if (!game.snapshots || game.snapshots.length === 0) {
        throw new Error("No snapshots in game data.");
      }

      loadingEl.classList.add("hidden");
      gameUI.classList.remove("hidden");

      var playerNames = (game.players || []).map(function (p) {
        var deck = p.deckName || p.commander || "";
        return deck ? p.name + " (" + deck + ")" : p.name;
      }).join(" vs ");
      gameTitleEl.textContent = playerNames;

      if (game.youtubeUrl) {
        var ytLink = document.getElementById("youtube-link");
        var ytUrl = document.getElementById("youtube-url");
        ytUrl.href = parseHttpUrl(game.youtubeUrl, "youtubeUrl");
        ytLink.classList.remove("hidden");
      }

      if (game.annotations && game.annotations.length > 0) {
        var counts = { questionable: 0, minor: 0, moderate: 0, major: 0 };
        game.annotations.forEach(function (a) { counts[a.severity] = (counts[a.severity] || 0) + 1; });
        var summaryEl = document.createElement("div");
        summaryEl.id = "blunder-summary";
        var parts = [];
        if (counts.major > 0) parts.push(counts.major + " major");
        if (counts.moderate > 0) parts.push(counts.moderate + " moderate");
        if (counts.minor > 0) parts.push(counts.minor + " minor");
        if (counts.questionable > 0) parts.push(counts.questionable + " questionable");
        var gameBlunderVersion = game.blunderScriptVersion || 1;
        var isOldBlunderAnalysis = gameBlunderVersion < minBlunderVersion;
        summaryEl.textContent = parts.join(", ") + " blunder" + (game.annotations.length !== 1 ? "s" : "");
        if (isOldBlunderAnalysis) {
          var oldTag = document.createElement("span");
          oldTag.className = "old-analysis-tag";
          oldTag.textContent = "(older analysis)";
          oldTag.title = "Analyzed with blunder script v" + gameBlunderVersion + " (min: v" + minBlunderVersion + ")";
          summaryEl.appendChild(document.createTextNode(" "));
          summaryEl.appendChild(oldTag);
        }
        document.getElementById("game-header").appendChild(summaryEl);
      }

      if (game.errors && game.errors.length > 0) {
        var errorSummaryEl = document.createElement("details");
        errorSummaryEl.id = "error-summary";
        var errorSummaryText = document.createElement("summary");
        errorSummaryText.textContent = game.errors.length + " critical error" + (game.errors.length !== 1 ? "s" : "");
        errorSummaryEl.appendChild(errorSummaryText);
        var errorList = document.createElement("ul");
        errorList.className = "error-list";
        game.errors.forEach(function (err) {
          var li = document.createElement("li");
          var text = "[" + (err.ts || "?") + "] [" + (err.source || "?") + "] " + (err.player || "?") + ": " + (err.message || "?");
          li.textContent = text;
          errorList.appendChild(li);
        });
        errorSummaryEl.appendChild(errorList);
        document.getElementById("game-header").appendChild(errorSummaryEl);
      }

      var gameSeason = game.season != null ? game.season : 0;
      var seasonEl = document.createElement("div");
      seasonEl.id = "season-info";
      seasonEl.textContent = "Season " + gameSeason;
      document.getElementById("game-header").appendChild(seasonEl);
      if (gameSeason === 0) {
        var seasonBanner = document.createElement("div");
        seasonBanner.id = "season-banner";
        seasonBanner.textContent =
          "This is a Season 0 game. MCP tools and priority semantics have changed" +
          " since this game was played, so its results are excluded from Season 1 ratings.";
        document.getElementById("game-header").after(seasonBanner);
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
        var auditContainer = document.getElementById("audit-container");
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
      loadingEl.classList.add("hidden");
      errorEl.textContent = err.message || "Failed to load game data.";
      errorEl.classList.remove("hidden");
    });
}
