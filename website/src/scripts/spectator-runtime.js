import "./game-renderer.js";

export function getGameRenderer() {
  var renderer = window.GameRenderer;
  if (!renderer) {
    throw new Error("game-renderer.js did not initialize window.GameRenderer");
  }
  return renderer;
}

export function getRequiredElement(root, selector, message) {
  var element = root.querySelector(selector);
  if (!element) {
    throw new Error(message || ("Missing " + selector));
  }
  return element;
}

export function getPreviewElements(root) {
  return {
    container: getRequiredElement(root, "#card-preview"),
    image: getRequiredElement(root, "#preview-image"),
    name: getRequiredElement(root, "#preview-name"),
    cost: getRequiredElement(root, "#preview-cost"),
    type: getRequiredElement(root, "#preview-type"),
    stats: getRequiredElement(root, "#preview-stats"),
    rules: getRequiredElement(root, "#preview-rules"),
  };
}

export function buildPlayerColorMap(players) {
  var playerColorMap = {};
  (players || []).forEach(function (player, index) {
    if (!player || typeof player.name !== "string") {
      return;
    }
    playerColorMap[player.name] = index % 4;
  });
  return playerColorMap;
}

export function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function colorizePlayerNames(message, playerColorMap, renderer) {
  var escaped = escapeHtml(message);
  var names = Object.keys(playerColorMap);
  names.sort(function (a, b) {
    return b.length - a.length;
  });
  names.forEach(function (name) {
    var cls = "action-" + renderer.PLAYER_COLORS[playerColorMap[name]];
    var escapedName = escapeHtml(name);
    escaped = escaped.split(escapedName).join('<span class="' + cls + '">' + escapedName + "</span>");
  });
  return escaped;
}

export function parseJsonAttribute(element, attributeName, message) {
  var raw = element.getAttribute(attributeName);
  if (raw == null) {
    throw new Error(message || ("Missing " + attributeName));
  }
  return JSON.parse(raw);
}

export function setupPreviewLifecycle(renderer, previewEls, options) {
  renderer.setupMousePreview(previewEls.container);
  window.addEventListener("blur", function () {
    renderer.hidePreview(previewEls);
  });
  if (options && options.hideOnEscape === false) {
    return;
  }
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      renderer.hidePreview(previewEls);
    }
  });
}
