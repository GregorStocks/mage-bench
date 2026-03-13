import {
  buildPlayerColorMap,
  colorizePlayerNames,
  escapeHtml,
  getGameRenderer,
  getPreviewElements,
  getRequiredElement,
  setupPreviewLifecycle,
} from "./spectator-runtime.js";

export function initLiveGamePage(options) {
  var visualizer = options && options.root ? options.root : document.getElementById("live-visualizer");
  if (!visualizer) {
    return;
  }

  var renderer = getGameRenderer();
  var params = new URLSearchParams(window.location.search);
  var apiBase = params.get("api") || window.location.origin;
  var pollMs = Math.max(250, Number(params.get("pollMs") || 700));
  var usePositioned = params.get("positions") === "1";
  var useMock = params.get("mock") === "1";
  var obsMode = params.get("obs") === "1";

  if (obsMode) {
    var nav = document.querySelector("nav");
    if (nav) {
      nav.style.display = "none";
    }
    var footer = document.querySelector("footer");
    if (footer) {
      footer.style.display = "none";
    }
    document.body.style.background = "transparent";
    var mainEl = document.querySelector("main");
    if (mainEl) {
      mainEl.style.padding = "0";
      mainEl.style.margin = "0";
    }
  }

  var statusEl = getRequiredElement(visualizer, "#connection-status");
  var gameUI = getRequiredElement(visualizer, "#game-ui");
  var turnInfoEl = getRequiredElement(visualizer, "#turn-info");
  var playersGrid = getRequiredElement(visualizer, "#players-grid");
  var stackSection = getRequiredElement(visualizer, "#stack-section");
  var stackCards = getRequiredElement(visualizer, "#stack-cards");
  var positionLayer = getRequiredElement(visualizer, "#position-layer");
  var logList = getRequiredElement(visualizer, "#log-list");
  var previewEls = getPreviewElements(visualizer);

  var requestInFlight = false;
  var playerColorMap = {};
  var playersInitialized = false;
  var prevState = null;
  var lastEventSeq = 0;
  var prevTurn = null;
  var prevPhase = null;
  var prevStep = null;
  var prevActivePlayer = null;
  var livePlayerTurnCounts = {};
  var curPlayerTurn = null;

  function initPlayerColors(players) {
    if (playersInitialized) {
      return;
    }
    playerColorMap = buildPlayerColorMap(players);
    playersInitialized = true;
  }

  var SPAM_RE = / skip attack$|^Attacker: .+ unblocked$/;

  function appendEvent(event) {
    if (event.type !== "player_chat" && SPAM_RE.test(event.message || "")) {
      return;
    }
    if (event.type === "player_chat") {
      var chatLine = document.createElement("div");
      chatLine.className = "chat-line";
      var fromIdx = playerColorMap[event.from];
      var fromCls = fromIdx != null ? "action-" + renderer.PLAYER_COLORS[fromIdx] : "";
      chatLine.innerHTML =
        '<span class="chat-from ' + fromCls + '">' + escapeHtml(event.from || "") + ":</span> " +
        escapeHtml(event.message || "");
      logList.appendChild(chatLine);
      return;
    }

    var actionLine = document.createElement("div");
    actionLine.className = "action-line";
    actionLine.innerHTML = colorizePlayerNames(event.message || "", playerColorMap, renderer);
    logList.appendChild(actionLine);
  }

  function processEvents(events) {
    if (!events || events.length === 0) {
      return;
    }
    var newEvents = events.filter(function (event) {
      return event.seq > lastEventSeq;
    });
    if (newEvents.length === 0) {
      return;
    }
    newEvents.sort(function (left, right) {
      return left.seq - right.seq;
    });
    newEvents.forEach(appendEvent);
    lastEventSeq = newEvents[newEvents.length - 1].seq;
    logList.scrollTop = logList.scrollHeight;
  }

  async function tick() {
    if (requestInFlight) {
      return;
    }
    requestInFlight = true;

    try {
      var stateUrl = useMock ? apiBase + "/api/mock-state" : apiBase + "/api/state";
      var response = await fetch(stateUrl, { cache: "no-store" });
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      var raw = await response.json();

      if (raw && raw.status === "waiting") {
        statusEl.textContent = "Waiting for game to start...";
        statusEl.classList.remove("error");
        return;
      }

      var state = renderer.normalizeLiveState(raw);

      if (!statusEl.classList.contains("hidden")) {
        statusEl.classList.add("hidden");
        gameUI.classList.remove("hidden");
      }

      initPlayerColors(state.players);
      processEvents(raw.events);

      var curTurn = state.turn;
      var curPhase = state.phase;
      var curStep = state.step;
      var curActive = state.active_player;
      if (curActive && (curTurn !== prevTurn || curActive !== prevActivePlayer)) {
        livePlayerTurnCounts[curActive] = (livePlayerTurnCounts[curActive] || 0) + 1;
      }
      curPlayerTurn = curActive ? (livePlayerTurnCounts[curActive] || null) : null;

      if (prevTurn !== null) {
        if (curTurn !== prevTurn) {
          var turnSeparator = document.createElement("div");
          turnSeparator.className = "turn-separator";
          turnSeparator.textContent = "\u2014 " + renderer.formatTurnLabel(curPlayerTurn, curActive) + " \u2014";
          logList.appendChild(turnSeparator);
        }
        if (curPhase !== prevPhase || curStep !== prevStep) {
          var label = renderer.formatPhaseStep(curPhase, curStep);
          if (label) {
            var phaseSeparator = document.createElement("div");
            phaseSeparator.className = "phase-separator";
            phaseSeparator.textContent = "\u2014 " + label + " \u2014";
            logList.appendChild(phaseSeparator);
          }
        }
        if (curTurn !== prevTurn || curPhase !== prevPhase || curStep !== prevStep) {
          logList.scrollTop = logList.scrollHeight;
        }
      }
      prevTurn = curTurn;
      prevPhase = curPhase;
      prevStep = curStep;
      prevActivePlayer = curActive;

      var diffs = prevState ? renderer.computeDiff(prevState, state) : null;
      prevState = state;

      renderer.renderStatusLine(turnInfoEl, state, curPlayerTurn);

      var isCommander = (state.players || []).some(function (player) {
        return player.commanders && player.commanders.length > 0;
      });

      if (usePositioned) {
        var rendered = renderer.renderPositionLayer(positionLayer, state, visualizer, previewEls);
        if (rendered) {
          playersGrid.classList.add("hidden");
        } else {
          playersGrid.classList.remove("hidden");
          positionLayer.classList.add("hidden");
          visualizer.classList.remove("positioned-mode");
          renderer.renderPlayers(playersGrid, state.players, {
            cardImages: {},
            playerColorMap: playerColorMap,
            diffs: diffs,
            previewEls: previewEls,
            showTimer: true,
            priorityPlayerName: state.priority_player,
            isCommander: isCommander,
          });
        }
      } else {
        renderer.renderPlayers(playersGrid, state.players, {
          cardImages: {},
          playerColorMap: playerColorMap,
          diffs: diffs,
          previewEls: previewEls,
          showTimer: true,
          priorityPlayerName: state.priority_player,
          isCommander: isCommander,
        });
      }

      renderer.renderStack(stackSection, stackCards, state.stack, {}, previewEls);
    } catch {
      statusEl.textContent = "Cannot reach game server at " + apiBase + " — retrying...";
      statusEl.classList.add("error");
      statusEl.classList.remove("hidden");
    } finally {
      requestInFlight = false;
    }
  }

  setupPreviewLifecycle(renderer, previewEls);

  if (usePositioned) {
    window.addEventListener("resize", function () {
      void tick();
    });
  }

  void tick();
  window.setInterval(function () {
    void tick();
  }, pollMs);
}
