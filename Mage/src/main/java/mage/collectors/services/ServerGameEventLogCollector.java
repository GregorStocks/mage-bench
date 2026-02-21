package mage.collectors.services;

import mage.MageObject;
import mage.abilities.Ability;
import mage.abilities.ActivatedAbility;
import mage.cards.Card;
import mage.choices.Choice;
import mage.constants.ManaType;
import mage.constants.PhaseStep;
import mage.constants.TurnPhase;
import mage.game.Game;
import mage.game.GameState;
import mage.game.combat.CombatGroup;
import mage.game.events.PlayerQueryEvent;
import mage.game.permanent.Permanent;
import mage.game.stack.StackObject;
import mage.players.Player;
import mage.util.MultiAmountMessage;
import mage.util.ShortIdRegistry;
import org.apache.log4j.Logger;
import org.jsoup.Jsoup;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Server-side game event log collector. Writes deterministic JSONL to
 * server_game_events.jsonl in the game log directory.
 *
 * Events: game_start, game_action, decision, game_end.
 * Decision events are query+response pairs: onPlayerQuery buffers the query,
 * onPlayerResponse combines and writes the full decision.
 */
public class ServerGameEventLogCollector extends EmptyDataCollector {

    private static final Logger logger = Logger.getLogger(ServerGameEventLogCollector.class);
    public static final String SERVICE_CODE = "serverGameEventLog";
    private static final String FILE_NAME = "server_game_events.jsonl";

    // Per-game writer, synchronized for thread safety between game thread and network thread
    private final Map<UUID, GameEventLogger> loggers = new ConcurrentHashMap<>();

    @Override
    public String getServiceCode() {
        return SERVICE_CODE;
    }

    @Override
    public String getInitInfo() {
        return "server-side game event log";
    }

    @Override
    public void onGameStart(Game game) {
        String gameLogDir = game.getOptions().gameLogDir;
        if (gameLogDir == null) {
            return;
        }
        GameEventLogger gel = new GameEventLogger(game.getId(), gameLogDir);
        loggers.put(game.getId(), gel);

        // Write game_start event
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("seq", 0);
        event.put("type", "game_start");

        List<Map<String, Object>> players = new ArrayList<>();
        for (Player player : game.getPlayers().values()) {
            Map<String, Object> p = new LinkedHashMap<>();
            p.put("name", player.getName());
            players.add(p);
        }
        event.put("players", players);
        gel.writeLine(toJson(event));
    }

    @Override
    public void onGameLog(Game game, String message, int gameSeq) {
        GameEventLogger gel = loggers.get(game.getId());
        if (gel == null) {
            return;
        }

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("seq", gameSeq);
        event.put("type", "game_action");
        event.put("message", stripHtml(message));
        gel.writeLine(toJson(event));
    }

    @Override
    public void onPlayerQuery(Game game, PlayerQueryEvent queryEvent, int gameSeq) {
        GameEventLogger gel = loggers.get(game.getId());
        if (gel == null) {
            return;
        }

        // Skip non-decision event types
        PlayerQueryEvent.QueryType qt = queryEvent.getQueryType();
        if (qt == PlayerQueryEvent.QueryType.PERSONAL_MESSAGE
                || qt == PlayerQueryEvent.QueryType.TOURNAMENT_CONSTRUCT
                || qt == PlayerQueryEvent.QueryType.DRAFT_PICK_CARD) {
            return;
        }

        // Buffer pending query for this player
        PendingQuery pending = new PendingQuery();
        pending.gameSeq = gameSeq;
        pending.queryType = qt;
        pending.playerId = queryEvent.getPlayerId();
        pending.message = queryEvent.getMessage();
        pending.event = queryEvent;
        pending.stateSnapshot = buildStateSnapshot(game, gameSeq);
        gel.setPendingQuery(queryEvent.getPlayerId(), pending);
    }

    @Override
    public void onPlayerResponse(Game game, UUID playerId, String responseType, Object data) {
        GameEventLogger gel = loggers.get(game.getId());
        if (gel == null) {
            return;
        }

        PendingQuery pending = gel.consumePendingQuery(playerId);
        if (pending == null) {
            // Response without a pending query — can happen for computer players
            return;
        }

        // Build decision event
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("seq", pending.gameSeq);
        event.put("type", "decision");
        event.put("query_type", pending.queryType.name());

        ShortIdRegistry registry = game.getShortIdRegistry();
        Player player = game.getPlayer(playerId);
        event.put("player", player != null ? player.getName() : playerId.toString());

        if (pending.message != null) {
            event.put("message", stripHtml(pending.message));
        }

        // Build choices structure
        Map<String, Object> choices = buildChoices(game, pending);
        if (choices != null && !choices.isEmpty()) {
            event.put("choices", choices);
        }

        // Build response structure
        Map<String, Object> response = buildResponse(game, responseType, data, pending);
        event.put("response", response);

        // State snapshot (deduped by hash)
        if (pending.stateSnapshot != null) {
            String hash = String.valueOf(pending.stateSnapshot.hashCode());
            if (!hash.equals(gel.getLastStateHash())) {
                event.put("state", pending.stateSnapshot);
                gel.setLastStateHash(hash);
            } else {
                event.put("state_hash", hash);
            }
        }

        gel.writeLine(toJson(event));
    }

    @Override
    public void onGameEnd(Game game) {
        GameEventLogger gel = loggers.get(game.getId());
        if (gel == null) {
            return;
        }

        int seq = game.nextGameSeq();
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("seq", seq);
        event.put("type", "game_end");

        // Find winner by checking player states
        String winnerName = null;
        for (Player p : game.getPlayers().values()) {
            if (p.hasWon()) {
                winnerName = p.getName();
                break;
            }
        }

        // Final life totals
        Map<String, Integer> lifeTotals = new LinkedHashMap<>();
        for (Player p : game.getPlayers().values()) {
            lifeTotals.put(p.getName(), p.getLife());
        }
        event.put("winner", winnerName);
        event.put("life_totals", lifeTotals);

        gel.writeLine(toJson(event));
        gel.close();
        loggers.remove(game.getId());
    }

    // --- Choices building per query type ---

    private Map<String, Object> buildChoices(Game game, PendingQuery pending) {
        Map<String, Object> choices = new LinkedHashMap<>();
        PlayerQueryEvent ev = pending.event;
        ShortIdRegistry registry = game.getShortIdRegistry();

        switch (pending.queryType) {
            case SELECT:
                // Playable objects available to play
                choices.put("can_pass", true);
                break;
            case ASK:
                choices.put("question", stripHtml(pending.message));
                break;
            case PICK_TARGET:
                if (ev.getTargets() != null) {
                    List<Map<String, Object>> targets = new ArrayList<>();
                    for (UUID targetId : ev.getTargets()) {
                        Map<String, Object> t = new LinkedHashMap<>();
                        t.put("id", registry.getOrAssign(targetId));
                        MageObject obj = game.getObject(targetId);
                        t.put("name", obj != null ? obj.getName() : "Unknown");
                        targets.add(t);
                    }
                    choices.put("targets", targets);
                }
                choices.put("required", ev.isRequired());
                break;
            case CHOOSE_ABILITY:
                if (ev.getAbilities() != null) {
                    List<Map<String, Object>> abilities = new ArrayList<>();
                    int idx = 0;
                    for (Ability ab : ev.getAbilities()) {
                        Map<String, Object> a = new LinkedHashMap<>();
                        a.put("index", idx++);
                        a.put("description", ab.getRule());
                        abilities.add(a);
                    }
                    choices.put("abilities", abilities);
                }
                break;
            case CHOOSE_CHOICE:
                if (ev.getChoice() != null) {
                    Choice c = ev.getChoice();
                    choices.put("options", new ArrayList<>(c.getChoices()));
                }
                break;
            case PLAY_MANA:
                choices.put("message", stripHtml(pending.message));
                break;
            case AMOUNT:
                choices.put("min", ev.getMin());
                choices.put("max", ev.getMax());
                break;
            case MULTI_AMOUNT:
                if (ev.getMessages() != null) {
                    List<Map<String, Object>> items = new ArrayList<>();
                    for (MultiAmountMessage msg : ev.getMessages()) {
                        Map<String, Object> item = new LinkedHashMap<>();
                        item.put("description", msg.message);
                        item.put("min", msg.min);
                        item.put("max", msg.max);
                        items.add(item);
                    }
                    choices.put("items", items);
                }
                choices.put("total_min", ev.getMin());
                choices.put("total_max", ev.getMax());
                break;
            case CHOOSE_PILE:
                if (ev.getPile1() != null) {
                    choices.put("pile1", cardListToNames(ev.getPile1()));
                }
                if (ev.getPile2() != null) {
                    choices.put("pile2", cardListToNames(ev.getPile2()));
                }
                break;
            case CHOOSE_MODE:
                if (ev.getModes() != null) {
                    List<Map<String, Object>> modes = new ArrayList<>();
                    for (Map.Entry<UUID, String> entry : ev.getModes().entrySet()) {
                        Map<String, Object> m = new LinkedHashMap<>();
                        m.put("description", entry.getValue());
                        modes.add(m);
                    }
                    choices.put("modes", modes);
                }
                break;
            default:
                break;
        }
        return choices;
    }

    private List<Map<String, String>> cardListToNames(List<? extends Card> cards) {
        List<Map<String, String>> result = new ArrayList<>();
        for (Card c : cards) {
            Map<String, String> m = new LinkedHashMap<>();
            m.put("name", c.getName());
            result.add(m);
        }
        return result;
    }

    // --- Response building ---

    private Map<String, Object> buildResponse(Game game, String responseType, Object data, PendingQuery pending) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("type", responseType);
        ShortIdRegistry registry = game.getShortIdRegistry();

        switch (responseType) {
            case "uuid":
                UUID uuid = (UUID) data;
                if (uuid == null) {
                    response.put("type", "pass");
                } else {
                    response.put("id", registry.getOrAssign(uuid));
                    MageObject obj = game.getObject(uuid);
                    if (obj != null) {
                        response.put("name", obj.getName());
                    }
                }
                break;
            case "boolean":
                response.put("value", data);
                break;
            case "string":
                response.put("value", data);
                break;
            case "integer":
                response.put("value", data);
                break;
            case "manaType":
                response.put("color", data != null ? data.toString() : null);
                break;
        }
        return response;
    }

    // --- State snapshot building ---

    private Map<String, Object> buildStateSnapshot(Game game, int gameSeq) {
        GameState state = game.getState();
        if (state == null) {
            return null;
        }
        ShortIdRegistry registry = game.getShortIdRegistry();
        Map<String, Object> snapshot = new LinkedHashMap<>();

        snapshot.put("turn", state.getTurnNum());
        TurnPhase phase = state.getTurnPhaseType();
        snapshot.put("phase", phase != null ? phase.name() : null);
        PhaseStep step = state.getTurnStepType();
        snapshot.put("step", step != null ? step.name() : null);

        Player activePlayer = state.getActivePlayerId() != null ? game.getPlayer(state.getActivePlayerId()) : null;
        snapshot.put("active_player", activePlayer != null ? activePlayer.getName() : null);
        Player priorityPlayer = state.getPriorityPlayerId() != null ? game.getPlayer(state.getPriorityPlayerId()) : null;
        snapshot.put("priority_player", priorityPlayer != null ? priorityPlayer.getName() : null);

        // Players
        List<Map<String, Object>> players = new ArrayList<>();
        // Sort by name for deterministic output
        List<Player> sortedPlayers = new ArrayList<>(state.getPlayers().values());
        sortedPlayers.sort(Comparator.comparing(Player::getName));

        for (Player player : sortedPlayers) {
            Map<String, Object> p = new LinkedHashMap<>();
            p.put("name", player.getName());
            p.put("life", player.getLife());
            p.put("library_size", player.getLibrary().size());

            // Hand — server has full visibility
            List<Map<String, Object>> hand = new ArrayList<>();
            List<Card> handCards = new ArrayList<>(player.getHand().getCards(game));
            handCards.sort(Comparator.comparing(Card::getName).thenComparingInt(c -> registry.getSequence(c.getId())));
            for (Card card : handCards) {
                Map<String, Object> ci = new LinkedHashMap<>();
                ci.put("id", registry.getOrAssign(card.getId()));
                ci.put("name", card.getName());
                if (card.getManaCost() != null) {
                    ci.put("mana_cost", card.getManaCost().getText());
                }
                hand.add(ci);
            }
            p.put("hand", hand);

            // Battlefield
            List<Map<String, Object>> battlefield = new ArrayList<>();
            List<Permanent> perms = new ArrayList<>();
            for (Permanent perm : game.getBattlefield().getAllActivePermanents(player.getId())) {
                perms.add(perm);
            }
            perms.sort(Comparator.comparing(Permanent::getName).thenComparingInt(perm -> registry.getSequence(perm.getId())));
            for (Permanent perm : perms) {
                Map<String, Object> pi = new LinkedHashMap<>();
                pi.put("id", registry.getOrAssign(perm.getId()));
                pi.put("name", perm.getName());
                pi.put("tapped", perm.isTapped());
                battlefield.add(pi);
            }
            p.put("battlefield", battlefield);

            // Graveyard
            List<Map<String, Object>> graveyard = new ArrayList<>();
            List<Card> gyCards = new ArrayList<>(player.getGraveyard().getCards(game));
            gyCards.sort(Comparator.comparing(Card::getName).thenComparingInt(c -> registry.getSequence(c.getId())));
            for (Card card : gyCards) {
                Map<String, Object> ci = new LinkedHashMap<>();
                ci.put("id", registry.getOrAssign(card.getId()));
                ci.put("name", card.getName());
                graveyard.add(ci);
            }
            p.put("graveyard", graveyard);

            // Exile
            List<Map<String, Object>> exile = new ArrayList<>();
            // Exile is zone-based, gather cards owned by this player
            for (Card card : game.getExile().getCardsOwned(game, player.getId())) {
                Map<String, Object> ci = new LinkedHashMap<>();
                ci.put("id", registry.getOrAssign(card.getId()));
                ci.put("name", card.getName());
                exile.add(ci);
            }
            exile.sort(Comparator.<Map<String, Object>, String>comparing(m -> (String) m.get("name"))
                    .thenComparing(m -> (String) m.get("id")));
            p.put("exile", exile);

            players.add(p);
        }
        snapshot.put("players", players);

        // Stack
        List<Map<String, Object>> stack = new ArrayList<>();
        for (StackObject so : state.getStack()) {
            Map<String, Object> si = new LinkedHashMap<>();
            si.put("id", registry.getOrAssign(so.getId()));
            si.put("name", so.getName());
            Player controller = game.getPlayer(so.getControllerId());
            si.put("controller", controller != null ? controller.getName() : null);
            stack.add(si);
        }
        snapshot.put("stack", stack);

        // Combat
        if (state.getCombat() != null && !state.getCombat().getGroups().isEmpty()) {
            List<Map<String, Object>> combat = new ArrayList<>();
            for (CombatGroup group : state.getCombat().getGroups()) {
                Map<String, Object> g = new LinkedHashMap<>();
                List<String> attackers = new ArrayList<>();
                for (UUID aid : group.getAttackers()) {
                    Permanent attacker = game.getPermanent(aid);
                    attackers.add(attacker != null ? attacker.getName() : registry.getOrAssign(aid));
                }
                g.put("attackers", attackers);
                List<String> blockers = new ArrayList<>();
                for (UUID bid : group.getBlockers()) {
                    Permanent blocker = game.getPermanent(bid);
                    blockers.add(blocker != null ? blocker.getName() : registry.getOrAssign(bid));
                }
                g.put("blockers", blockers);
                combat.add(g);
            }
            snapshot.put("combat", combat);
        }

        return snapshot;
    }

    // --- JSON serialization (simple, no dependency) ---

    private static String toJson(Object obj) {
        StringBuilder sb = new StringBuilder();
        appendJson(sb, obj);
        return sb.toString();
    }

    @SuppressWarnings("unchecked")
    private static void appendJson(StringBuilder sb, Object obj) {
        if (obj == null) {
            sb.append("null");
        } else if (obj instanceof String) {
            sb.append('"');
            escapeJson(sb, (String) obj);
            sb.append('"');
        } else if (obj instanceof Number || obj instanceof Boolean) {
            sb.append(obj);
        } else if (obj instanceof Map) {
            Map<String, Object> map = (Map<String, Object>) obj;
            sb.append('{');
            boolean first = true;
            // Use sorted keys for deterministic output
            List<String> keys = new ArrayList<>(map.keySet());
            // Preserve insertion order for LinkedHashMap (don't sort)
            if (!(map instanceof LinkedHashMap)) {
                Collections.sort(keys);
            }
            for (String key : keys) {
                if (!first) sb.append(',');
                first = false;
                sb.append('"');
                escapeJson(sb, key);
                sb.append("\":");
                appendJson(sb, map.get(key));
            }
            sb.append('}');
        } else if (obj instanceof List) {
            List<?> list = (List<?>) obj;
            sb.append('[');
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) sb.append(',');
                appendJson(sb, list.get(i));
            }
            sb.append(']');
        } else if (obj instanceof Enum) {
            sb.append('"');
            sb.append(obj.toString());
            sb.append('"');
        } else {
            sb.append('"');
            escapeJson(sb, obj.toString());
            sb.append('"');
        }
    }

    private static void escapeJson(StringBuilder sb, String s) {
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
    }

    private static String stripHtml(String html) {
        if (html == null) return null;
        return Jsoup.parse(html).text();
    }

    // --- Per-game logger ---

    private static class GameEventLogger {
        private final Path filePath;
        private BufferedWriter writer;
        private String lastStateHash;
        // Pending queries per player (game thread writes, network thread reads)
        private final Map<UUID, PendingQuery> pendingQueries = new ConcurrentHashMap<>();

        GameEventLogger(UUID gameId, String gameLogDir) {
            this.filePath = Paths.get(gameLogDir, FILE_NAME);
            try {
                Files.createDirectories(filePath.getParent());
                this.writer = Files.newBufferedWriter(filePath, StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
            } catch (IOException e) {
                logger.error("Failed to create server game event log: " + filePath, e);
                this.writer = null;
            }
        }

        synchronized void writeLine(String json) {
            if (writer == null) return;
            try {
                writer.write(json);
                writer.newLine();
                writer.flush();
            } catch (IOException e) {
                logger.error("Failed to write to server game event log: " + filePath, e);
            }
        }

        void setPendingQuery(UUID playerId, PendingQuery query) {
            pendingQueries.put(playerId, query);
        }

        PendingQuery consumePendingQuery(UUID playerId) {
            return pendingQueries.remove(playerId);
        }

        String getLastStateHash() {
            return lastStateHash;
        }

        void setLastStateHash(String hash) {
            this.lastStateHash = hash;
        }

        synchronized void close() {
            if (writer != null) {
                try {
                    writer.close();
                } catch (IOException e) {
                    logger.error("Failed to close server game event log: " + filePath, e);
                }
                writer = null;
            }
        }
    }

    // --- Pending query buffer ---

    private static class PendingQuery {
        int gameSeq;
        PlayerQueryEvent.QueryType queryType;
        UUID playerId;
        String message;
        PlayerQueryEvent event;
        Map<String, Object> stateSnapshot;
    }
}
