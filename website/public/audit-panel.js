/**
 * audit-panel.js — inline blunder audit panel for the game viewer.
 *
 * Loaded conditionally when the game viewer runs in audit mode
 * (AUDIT_API_PORT env var is set). Adds verdict controls below
 * the game viewer so you can audit decisions without leaving the page.
 *
 * Usage:
 *   var panel = AuditPanel.create(container, viewer, slug, {
 *     initialDecision: 17,          // from ?d=N
 *     onDecisionChange: function(di) { ... },
 *   });
 */
(function (root) {
  "use strict";

  var API_PREFIX = "/api/audit";

  // ── Helpers ──

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function fetchJson(url, opts) {
    return fetch(url, opts).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.error || r.statusText); });
      return r.json();
    });
  }

  // ── DOM construction ──

  function buildDOM(container) {
    var html = [
      '<div id="audit-panel" class="audit-hidden">',
      '  <div id="audit-decision-context">',
      '    <div id="audit-stats"></div>',
      '    <div id="audit-decision-message"></div>',
      '    <div id="audit-decision-chosen"></div>',
      '    <div id="audit-annotation-box" class="hidden">',
      '      <span class="ann-severity" id="audit-ann-severity"></span>',
      '      <div id="audit-annotation-desc"></div>',
      '      <div id="audit-annotation-details"></div>',
      '    </div>',
      '  </div>',
      '  <div id="audit-verdict-panel">',
      '    <div id="audit-nav-buttons">',
      '      <button id="audit-prev-btn" disabled title="Previous unaudited">&larr; Prev</button>',
      '      <button id="audit-next-btn" disabled title="Next unaudited">Next &rarr;</button>',
      '    </div>',
      '    <button id="audit-mark-btn" disabled>Mark Current Decision<span class="audit-shortcut-hint">[M]</span></button>',
      '    <div id="audit-existing-verdict" class="hidden"></div>',
      '    <div id="audit-existing-notes" class="hidden"></div>',
      '    <div id="audit-verdict-buttons">',
      '      <button class="audit-verdict-btn" id="audit-btn-blunder" data-verdict="blunder">Blunder<span class="audit-shortcut-hint">[B]</span></button>',
      '      <button class="audit-verdict-btn" id="audit-btn-not-blunder" data-verdict="not_blunder">Not Blunder<span class="audit-shortcut-hint">[N]</span></button>',
      '      <button class="audit-verdict-btn" id="audit-btn-questionable" data-verdict="questionable">Questionable<span class="audit-shortcut-hint">[?]</span></button>',
      '    </div>',
      '    <textarea id="audit-notes-input" placeholder="Notes (optional)"></textarea>',
      '    <button id="audit-submit-btn" disabled>Submit<span class="audit-shortcut-hint">[Enter]</span></button>',
      '    <div id="audit-verdict-status"></div>',
      '  </div>',
      '</div>',
      '<div id="audit-disambig-overlay" class="hidden">',
      '  <div id="audit-disambig-box">',
      '    <div id="audit-disambig-title">Multiple decisions at this snapshot</div>',
      '    <div id="audit-disambig-list"></div>',
      '    <button id="audit-disambig-cancel">Cancel</button>',
      '  </div>',
      '</div>',
    ].join("\n");

    container.insertAdjacentHTML("beforeend", html);

    return {
      panel: container.querySelector("#audit-panel"),
      stats: container.querySelector("#audit-stats"),
      message: container.querySelector("#audit-decision-message"),
      chosen: container.querySelector("#audit-decision-chosen"),
      annotationBox: container.querySelector("#audit-annotation-box"),
      annSeverity: container.querySelector("#audit-ann-severity"),
      annotationDesc: container.querySelector("#audit-annotation-desc"),
      annotationDetails: container.querySelector("#audit-annotation-details"),
      existingVerdict: container.querySelector("#audit-existing-verdict"),
      existingNotes: container.querySelector("#audit-existing-notes"),
      verdictButtons: container.querySelectorAll(".audit-verdict-btn"),
      notesInput: container.querySelector("#audit-notes-input"),
      submitBtn: container.querySelector("#audit-submit-btn"),
      verdictStatus: container.querySelector("#audit-verdict-status"),
      markBtn: container.querySelector("#audit-mark-btn"),
      prevBtn: container.querySelector("#audit-prev-btn"),
      nextBtn: container.querySelector("#audit-next-btn"),
      disambigOverlay: container.querySelector("#audit-disambig-overlay"),
      disambigList: container.querySelector("#audit-disambig-list"),
      disambigCancel: container.querySelector("#audit-disambig-cancel"),
    };
  }

  // ── Panel creation ──

  function create(container, viewer, slug, options) {
    options = options || {};
    var onDecisionChange = options.onDecisionChange || function () {};

    var dom = buildDOM(container);

    // State
    var gamePlays = [];       // all plays for this game from API
    var currentDetail = null; // loaded detail for current decision
    var selectedVerdict = null;
    var submitting = false;

    // ── Show/hide ──

    function show() { dom.panel.classList.remove("audit-hidden"); }
    function hide() { dom.panel.classList.add("audit-hidden"); }

    // ── Data loading ──

    function loadPlays() {
      return fetchJson(API_PREFIX + "/plays?game=" + encodeURIComponent(slug))
        .then(function (plays) {
          gamePlays = plays;
          updateNav();
        })
        .catch(function (e) {
          dom.verdictStatus.textContent = "Audit API unavailable: " + e.message;
          show();
        });
    }

    function loadStats() {
      fetchJson(API_PREFIX + "/stats").then(function (s) {
        dom.stats.textContent = s.audited + "/" + s.total + " audited \u00b7 " +
          s.unaudited + " remaining";
      }).catch(function () {
        dom.stats.textContent = "";
      });
    }

    function loadDetail(di) {
      dom.verdictStatus.textContent = "Loading...";
      show();

      fetchJson(API_PREFIX + "/plays/" + slug + "/" + di)
        .then(function (detail) {
          currentDetail = detail;
          renderContext(detail);
          // Navigate the viewer to the aftermath snapshot
          if (viewer && detail.aftermath_index != null) {
            viewer.goTo(detail.aftermath_index);
          }
          onDecisionChange(di);
          dom.markBtn.disabled = false;
        })
        .catch(function (e) {
          dom.verdictStatus.textContent = "Error: " + e.message;
        });
    }

    // ── Context rendering ──

    function renderContext(d) {
      dom.message.textContent = d.message || "";
      dom.chosen.innerHTML = '<span class="label">Chosen: </span><span class="value">' +
        escapeHtml(d.chosen || "?") + '</span>' +
        ' <span class="label">Hand: </span><span class="value">' +
        escapeHtml(d.hand || "?") + '</span>';

      // Annotation
      if (d.annotation && d.annotation.severity) {
        dom.annotationBox.classList.remove("hidden");
        dom.annSeverity.textContent = d.annotation.severity;
        dom.annSeverity.className = "ann-severity " + d.annotation.severity;
        dom.annotationDesc.textContent = d.annotation.description || "";

        var detailsHtml = "";
        if (d.annotation.actionTaken) {
          detailsHtml += '<div><span class="detail-label">Action taken: </span><span class="detail-value">' +
            escapeHtml(d.annotation.actionTaken) + '</span></div>';
        }
        if (d.annotation.betterLine) {
          detailsHtml += '<div><span class="detail-label">Better line: </span><span class="detail-value">' +
            escapeHtml(d.annotation.betterLine) + '</span></div>';
        }
        dom.annotationDetails.innerHTML = detailsHtml;
      } else {
        dom.annotationBox.classList.add("hidden");
      }

      // Existing verdict
      if (d.verdict) {
        dom.existingVerdict.className = "v-" + d.verdict;
        dom.existingVerdict.classList.remove("hidden");
        dom.existingVerdict.textContent = "Current: " + d.verdict.replace(/_/g, " ");
        if (d.human_notes) {
          dom.existingNotes.classList.remove("hidden");
          dom.existingNotes.textContent = '"' + d.human_notes + '"';
        } else {
          dom.existingNotes.classList.add("hidden");
        }
      } else {
        dom.existingVerdict.classList.add("hidden");
        dom.existingNotes.classList.add("hidden");
      }

      // Reset verdict selection
      selectedVerdict = null;
      dom.verdictButtons.forEach(function (b) { b.classList.remove("selected"); });
      dom.notesInput.value = d.human_notes || "";
      dom.submitBtn.disabled = true;
      dom.verdictStatus.textContent = "";
      updateNav();
    }

    // ── Verdict selection & submission ──

    function selectVerdict(v) {
      selectedVerdict = v;
      dom.verdictButtons.forEach(function (b) {
        b.classList.toggle("selected", b.dataset.verdict === v);
      });
      dom.submitBtn.disabled = false;
      if (!dom.notesInput.value) dom.notesInput.focus();
    }

    function submitVerdict() {
      if (!selectedVerdict || !currentDetail || submitting) return;
      submitting = true;
      dom.submitBtn.disabled = true;
      dom.verdictStatus.innerHTML = '<span class="audit-spinner"></span> Saving...';

      var body = {
        verdict: selectedVerdict,
        notes: dom.notesInput.value.trim() || null,
      };

      fetchJson(API_PREFIX + "/plays/" + currentDetail.game_id + "/" + currentDetail.decision_index + "/verdict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      .then(function () {
        dom.verdictStatus.textContent = "Saved: " + selectedVerdict.replace(/_/g, " ");
        // Update local play list
        gamePlays.forEach(function (p) {
          if (p.decision_index === currentDetail.decision_index) {
            p.verdict = selectedVerdict;
          }
        });
        currentDetail.verdict = selectedVerdict;
        renderContext(currentDetail);
        loadStats();
        submitting = false;
        // Auto-advance to next unaudited
        advanceNext();
      })
      .catch(function (e) {
        dom.verdictStatus.textContent = "Error: " + e.message;
        dom.submitBtn.disabled = false;
        submitting = false;
      });
    }

    // ── Navigation ──

    function findAdjacentUnaudited(direction) {
      if (!currentDetail || gamePlays.length === 0) return null;
      var curIdx = -1;
      for (var i = 0; i < gamePlays.length; i++) {
        if (gamePlays[i].decision_index === currentDetail.decision_index) {
          curIdx = i;
          break;
        }
      }
      if (curIdx < 0) return null;

      var len = gamePlays.length;
      for (var step = 1; step < len; step++) {
        var idx = direction > 0
          ? (curIdx + step) % len
          : (curIdx - step + len) % len;
        if (gamePlays[idx].verdict == null) {
          return gamePlays[idx].decision_index;
        }
      }
      return null;
    }

    function advancePrev() {
      var di = findAdjacentUnaudited(-1);
      if (di != null) loadDetail(di);
      else dom.verdictStatus.textContent = "No more unaudited in this game";
    }

    function advanceNext() {
      var di = findAdjacentUnaudited(1);
      if (di != null) loadDetail(di);
      else dom.verdictStatus.textContent = "All audited in this game!";
    }

    function updateNav() {
      var hasPrev = findAdjacentUnaudited(-1) != null;
      var hasNext = findAdjacentUnaudited(1) != null;
      dom.prevBtn.disabled = !hasPrev;
      dom.nextBtn.disabled = !hasNext;
    }

    // ── Mark current decision ──

    function markCurrentDecision() {
      if (!viewer) return;
      var snapIdx = viewer.getCurrentIndex();
      dom.verdictStatus.textContent = "Finding decisions at snapshot " + snapIdx + "...";

      fetchJson(API_PREFIX + "/decisions-at-snapshot/" + slug + "/" + snapIdx)
        .then(function (decisions) {
          if (decisions.length === 0) {
            dom.verdictStatus.textContent = "No decision at snapshot " + snapIdx;
            return;
          }
          if (decisions.length === 1) {
            loadDetail(decisions[0].decision_index);
            return;
          }
          showDisambiguation(decisions);
        })
        .catch(function (e) {
          dom.verdictStatus.textContent = "Error: " + e.message;
        });
    }

    function showDisambiguation(decisions) {
      dom.disambigList.innerHTML = "";
      decisions.forEach(function (d) {
        var item = document.createElement("div");
        item.className = "audit-disambig-item";
        item.innerHTML =
          '<span class="audit-disambig-player">' + escapeHtml(d.player) + '</span>' +
          ' <span class="audit-disambig-turn">T' + d.turn + ' ' + escapeHtml(d.phase || '') + '</span>' +
          '<div class="audit-disambig-message">' + escapeHtml(d.message) + '</div>' +
          '<div class="audit-disambig-chosen">Chose: ' + escapeHtml(d.chosen) + '</div>';
        item.addEventListener("click", function () {
          dom.disambigOverlay.classList.add("hidden");
          loadDetail(d.decision_index);
        });
        dom.disambigList.appendChild(item);
      });
      dom.disambigOverlay.classList.remove("hidden");
    }

    // ── Event listeners ──

    dom.verdictButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        selectVerdict(btn.dataset.verdict);
      });
    });

    dom.submitBtn.addEventListener("click", submitVerdict);
    dom.markBtn.addEventListener("click", markCurrentDecision);
    dom.prevBtn.addEventListener("click", advancePrev);
    dom.nextBtn.addEventListener("click", advanceNext);

    dom.disambigCancel.addEventListener("click", function () {
      dom.disambigOverlay.classList.add("hidden");
    });
    dom.disambigOverlay.addEventListener("click", function (e) {
      if (e.target === dom.disambigOverlay) dom.disambigOverlay.classList.add("hidden");
    });

    // ── Keyboard shortcuts ──

    document.addEventListener("keydown", function (e) {
      // Close disambiguation on Escape
      if (e.key === "Escape" && !dom.disambigOverlay.classList.contains("hidden")) {
        dom.disambigOverlay.classList.add("hidden");
        e.preventDefault();
        return;
      }
      // Don't intercept when typing in notes
      if (e.target === dom.notesInput) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          submitVerdict();
        }
        return;
      }
      // Don't intercept when in other inputs or viewer
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      switch (e.key) {
        case "b":
        case "B":
          if (!currentDetail) break;
          selectVerdict("blunder");
          e.preventDefault();
          break;
        case "n":
        case "N":
          if (!currentDetail) break;
          selectVerdict("not_blunder");
          e.preventDefault();
          break;
        case "?":
          if (!currentDetail) break;
          selectVerdict("questionable");
          e.preventDefault();
          break;
        case "Enter":
          if (!currentDetail) break;
          e.preventDefault();
          submitVerdict();
          break;
        case "m":
        case "M":
          e.preventDefault();
          markCurrentDecision();
          break;
        case "s":
        case "S":
          if (!currentDetail) break;
          e.preventDefault();
          advanceNext();
          break;
      }
    });

    // ── Init ──

    show();
    dom.markBtn.disabled = false;

    loadStats();
    loadPlays().then(function () {
      if (options.initialDecision != null) {
        loadDetail(options.initialDecision);
      }
    });

    // ── Public API ──

    return {
      loadDetail: loadDetail,
      markCurrentDecision: markCurrentDecision,
      destroy: function () {
        container.querySelector("#audit-panel").remove();
        container.querySelector("#audit-disambig-overlay").remove();
      },
    };
  }

  // ── Export ──

  var AuditPanel = { create: create };

  if (typeof root !== "undefined" && root !== null) {
    root.AuditPanel = AuditPanel;
  }

})(typeof window !== "undefined" ? window : undefined);
