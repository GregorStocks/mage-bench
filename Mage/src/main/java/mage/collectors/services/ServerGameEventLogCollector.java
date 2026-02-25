package mage.collectors.services;

import mage.MageObject;
import mage.abilities.Ability;
import mage.abilities.ActivatedAbility;
import mage.cards.Card;
import mage.choices.Choice;
import mage.constants.CardType;
import mage.constants.ManaType;
import mage.constants.PhaseStep;
import mage.constants.SubType;
import mage.constants.SuperType;
import mage.constants.TurnPhase;
import mage.counters.Counter;
import mage.counters.Counters;
import mage.designations.DesignationType;
import mage.game.Game;
import mage.game.GameState;
import mage.game.combat.CombatGroup;
import mage.game.events.PlayerQueryEvent;
import mage.game.permanent.Permanent;
import mage.game.permanent.PermanentToken;
import mage.game.stack.StackObject;
import mage.players.ManaPool;
import mage.players.Player;
import mage.target.Target;
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

        // Pre-assign short IDs to player UUIDs in sorted name order.
        // Player UUIDs appear as targets in early events (e.g. "Select a starting player")
        // and the assignment order must be deterministic regardless of join order.
        ShortIdRegistry registry = game.getShortIdRegistry();
        List<Player> sortedPlayers = new ArrayList<>(game.getPlayers().values());
        sortedPlayers.sort(Comparator.comparing(Player::getName));
        for (Player player : sortedPlayers) {
            registry.getOrAssign(player.getId());
        }

        // Write game_start event
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("seq", 0);
        event.put("type", "game_start");

        List<Map<String, Object>> players = new ArrayList<>();
        for (Player player : sortedPlayers) {
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

        // Emit turn_change / phase_change events when game state advances
        GameState state = game.getState();
        if (state == null) return;
        int turn = state.getTurnNum();
        TurnPhase phase = state.getTurnPhaseType();
        PhaseStep step = state.getTurnStepType();

        if (turn != gel.lastTurn || phase != gel.lastPhase || step != gel.lastStep) {
            Player active = state.getActivePlayerId() != null ? game.getPlayer(state.getActivePlayerId()) : null;
            String activeName = active != null ? active.getName() : null;

            // Emit turn_change when the turn number advances
            if (turn != gel.lastTurn) {
                Map<String, Object> turnEvent = new LinkedHashMap<>();
                turnEvent.put("seq", gameSeq);
                turnEvent.put("type", "turn_change");
                turnEvent.put("turn", turn);
                turnEvent.put("active_player", activeName);
                gel.writeLine(toJson(turnEvent));
            }

            Map<String, Object> phaseEvent = new LinkedHashMap<>();
            phaseEvent.put("seq", gameSeq);
            phaseEvent.put("type", "phase_change");
            phaseEvent.put("turn", turn);
            phaseEvent.put("phase", phase != null ? phase.name() : null);
            phaseEvent.put("step", step != null ? step.name() : null);
            phaseEvent.put("active_player", activeName);
            gel.writeLine(toJson(phaseEvent));
            gel.lastTurn = turn;
            gel.lastPhase = phase;
            gel.lastStep = step;
        }
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

        // Final state snapshot — captures life totals after combat damage resolves.
        // Without this, games ending via lethal damage have no snapshot showing the
        // final life total (the last decision snapshot is taken before damage).
        Map<String, Object> finalState = buildStateSnapshot(game, seq);
        if (finalState != null) {
            event.put("state", finalState);
        }

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

    /**
     * Build a type line string from a MageObject, e.g. "Legendary Creature - Bear Warrior".
     */
    private static String buildTypeLine(MageObject obj, Game game) {
        StringBuilder sb = new StringBuilder();
        for (SuperType st : obj.getSuperType(game)) {
            if (sb.length() > 0) sb.append(' ');
            sb.append(st.toString());
        }
        for (CardType ct : obj.getCardType(game)) {
            if (sb.length() > 0) sb.append(' ');
            sb.append(ct.toString());
        }
        List<SubType> subtypes = new ArrayList<>();
        for (SubType sub : obj.getSubtype(game)) {
            subtypes.add(sub);
        }
        if (!subtypes.isEmpty()) {
            sb.append(" — ");
            for (int i = 0; i < subtypes.size(); i++) {
                if (i > 0) sb.append(' ');
                sb.append(subtypes.get(i).toString());
            }
        }
        return sb.toString();
    }

    /**
     * Serialize counters map, returning null if empty.
     * Example: {"p1p1": 2, "loyalty": 3}
     */
    private static Map<String, Object> serializeCounters(Counters counters) {
        if (counters == null || counters.isEmpty()) {
            return null;
        }
        Map<String, Object> result = new LinkedHashMap<>();
        List<String> names = new ArrayList<>(counters.keySet());
        Collections.sort(names);
        for (String name : names) {
            Counter c = counters.get(name);
            if (c.getCount() > 0) {
                result.put(name, c.getCount());
            }
        }
        return result.isEmpty() ? null : result;
    }

    /**
     * Check whether a card has been modified from its oracle (printed) version.
     * Copies, tokens, and cards with in-game modifications to type, P/T, or rules are "modified".
     */
    private static boolean isCardModified(Card card, Game game) {
        if (card.isCopy()) return true;
        if (!Objects.equals(card.getRules(), card.getRules(game))) return true;
        if (!Objects.equals(card.getCardType(), card.getCardType(game))) return true;
        if (!Objects.equals(card.getSubtype(), card.getSubtype(game))) return true;
        if (!Objects.equals(card.getSuperType(), card.getSuperType(game))) return true;
        if (card.getPower().getValue() != card.getPower().getBaseValue()) return true;
        if (card.getToughness().getValue() != card.getToughness().getBaseValue()) return true;
        return false;
    }

    /**
     * Serialize a Card for hand/graveyard/exile zones.
     * Unmodified cards emit compact {id, name}; modified cards include full properties.
     */
    private static Map<String, Object> serializeCard(Card card, Game game, ShortIdRegistry registry) {
        Map<String, Object> ci = new LinkedHashMap<>();
        ci.put("id", registry.getOrAssign(card.getId()));
        ci.put("name", card.getName());

        if (isCardModified(card, game)) {
            if (card.getManaCost() != null) {
                ci.put("mana_cost", card.getManaCost().getText());
            }
            ci.put("type_line", buildTypeLine(card, game));
            if (card.isCreature(game)) {
                ci.put("power", card.getPower().getValue());
                ci.put("toughness", card.getToughness().getValue());
            }
            List<String> rules = card.getRules(game);
            if (rules != null && !rules.isEmpty()) {
                ci.put("rules", new ArrayList<>(rules));
            }
        }

        return ci;
    }

    /**
     * Serialize a Permanent for the battlefield zone.
     * Oracle-derivable properties (type_line, mana_cost, rules) are only emitted
     * when the permanent is modified from its printed card. Battlefield-specific
     * state (tapped, summoning_sick, P/T, counters, etc.) is always emitted.
     */
    private static Map<String, Object> serializePermanent(Permanent perm, Game game, ShortIdRegistry registry) {
        Map<String, Object> pi = new LinkedHashMap<>();
        pi.put("id", registry.getOrAssign(perm.getId()));
        pi.put("name", perm.getName());
        pi.put("tapped", perm.isTapped());

        // P/T for creatures
        if (perm.isCreature(game)) {
            pi.put("power", perm.getPower().getValue());
            pi.put("toughness", perm.getToughness().getValue());
        }
        // Loyalty for planeswalkers
        if (perm.isPlaneswalker(game)) {
            Counters counters = perm.getCounters(game);
            if (counters != null && counters.containsKey("loyalty")) {
                pi.put("loyalty", counters.getCount("loyalty"));
            }
        }

        boolean isToken = perm instanceof PermanentToken;
        pi.put("token", isToken);
        boolean faceDown = perm.isFaceDown(game);
        pi.put("face_down", faceDown);
        pi.put("summoning_sick", perm.hasSummoningSickness());

        // Oracle-derivable properties: only emit when modified from printed card.
        // Tokens are always "modified" (no oracle card to reference).
        boolean modified = isToken || isCardModified(perm, game);
        if (modified) {
            pi.put("type_line", buildTypeLine(perm, game));
            if (perm.getManaCost() != null) {
                pi.put("mana_cost", perm.getManaCost().getText());
            }
            List<String> rules = perm.getRules(game);
            if (rules != null && !rules.isEmpty()) {
                pi.put("rules", new ArrayList<>(rules));
            }
        }

        // Counters (exclude loyalty — already shown as top-level field)
        Counters counters = perm.getCounters(game);
        if (counters != null && !counters.isEmpty()) {
            Map<String, Object> counterMap = new LinkedHashMap<>();
            List<String> names = new ArrayList<>(counters.keySet());
            Collections.sort(names);
            for (String name : names) {
                if (name.equals("loyalty")) continue;
                Counter c = counters.get(name);
                if (c.getCount() > 0) {
                    counterMap.put(name, c.getCount());
                }
            }
            if (!counterMap.isEmpty()) {
                pi.put("counters", counterMap);
            }
        }

        // Attached to
        if (perm.getAttachedTo() != null) {
            MageObject attachedObj = game.getObject(perm.getAttachedTo());
            if (attachedObj != null) {
                pi.put("attached_to", registry.getOrAssign(perm.getAttachedTo()));
            }
        }

        // Visibility annotation for face-down permanents
        if (faceDown) {
            Player controller = game.getPlayer(perm.getControllerId());
            if (controller != null) {
                List<String> visibleTo = new ArrayList<>();
                visibleTo.add(controller.getName());
                pi.put("visible_to", visibleTo);
            }
        }

        return pi;
    }

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

            // Mana pool
            ManaPool pool = player.getManaPool();
            Map<String, Object> manaMap = new LinkedHashMap<>();
            if (pool.getWhite() > 0) manaMap.put("W", pool.getWhite());
            if (pool.getBlue() > 0) manaMap.put("U", pool.getBlue());
            if (pool.getBlack() > 0) manaMap.put("B", pool.getBlack());
            if (pool.getRed() > 0) manaMap.put("R", pool.getRed());
            if (pool.getGreen() > 0) manaMap.put("G", pool.getGreen());
            if (pool.getColorless() > 0) manaMap.put("C", pool.getColorless());
            if (!manaMap.isEmpty()) {
                p.put("mana_pool", manaMap);
            }

            // Player counters
            Counters playerCounters = player.getCountersAsCopy();
            Map<String, Object> pcMap = serializeCounters(playerCounters);
            if (pcMap != null) {
                p.put("counters", pcMap);
            }

            // Designations
            if (player.hasDesignation(DesignationType.THE_MONARCH)) {
                p.put("monarch", true);
            }
            if (player.hasDesignation(DesignationType.THE_INITIATIVE)) {
                p.put("initiative", true);
            }
            if (player.hasDesignation(DesignationType.CITYS_BLESSING)) {
                p.put("citys_blessing", true);
            }

            // Command zone
            Set<UUID> commanderIds = player.getCommandersIds();
            if (commanderIds != null && !commanderIds.isEmpty()) {
                List<Map<String, Object>> cmdZone = new ArrayList<>();
                for (UUID cmdId : commanderIds) {
                    MageObject cmdObj = game.getObject(cmdId);
                    if (cmdObj != null) {
                        Map<String, Object> cmdCard = new LinkedHashMap<>();
                        cmdCard.put("id", registry.getOrAssign(cmdId));
                        cmdCard.put("name", cmdObj.getName());
                        cmdZone.add(cmdCard);
                    }
                }
                if (!cmdZone.isEmpty()) {
                    cmdZone.sort(Comparator.<Map<String, Object>, String>comparing(m -> (String) m.get("name"))
                            .thenComparingInt(m -> ShortIdRegistry.parseSequence((String) m.get("id"))));
                    p.put("command_zone", cmdZone);
                }
            }

            // Hand — server has full visibility, annotated with visible_to
            // Pre-sort by name for deterministic ID assignment of unique-name cards,
            // then post-sort by (name, shortId) for same-name card ordering.
            // See ShortIdRegistry for the deterministic ordering invariant.
            List<Map<String, Object>> hand = new ArrayList<>();
            List<Card> handCards = new ArrayList<>(player.getHand().getCards(game));
            handCards.sort(Comparator.comparing(Card::getName));
            for (Card card : handCards) {
                Map<String, Object> ci = serializeCard(card, game, registry);
                // Hand cards are only visible to their owner
                List<String> visibleTo = new ArrayList<>();
                visibleTo.add(player.getName());
                ci.put("visible_to", visibleTo);
                hand.add(ci);
            }
            hand.sort(Comparator.<Map<String, Object>, String>comparing(m -> (String) m.get("name"))
                    .thenComparingInt(m -> ShortIdRegistry.parseSequence((String) m.get("id"))));
            p.put("hand", hand);

            // Battlefield — pre-sort by name, post-sort by (name, shortId)
            List<Map<String, Object>> battlefield = new ArrayList<>();
            List<Permanent> perms = new ArrayList<>();
            for (Permanent perm : game.getBattlefield().getAllActivePermanents(player.getId())) {
                perms.add(perm);
            }
            perms.sort(Comparator.comparing(Permanent::getName));
            for (Permanent perm : perms) {
                battlefield.add(serializePermanent(perm, game, registry));
            }
            battlefield.sort(Comparator.<Map<String, Object>, String>comparing(m -> (String) m.get("name"))
                    .thenComparingInt(m -> ShortIdRegistry.parseSequence((String) m.get("id"))));
            p.put("battlefield", battlefield);

            // Graveyard — pre-sort by name, post-sort by (name, shortId)
            List<Map<String, Object>> graveyard = new ArrayList<>();
            List<Card> gyCards = new ArrayList<>(player.getGraveyard().getCards(game));
            gyCards.sort(Comparator.comparing(Card::getName));
            for (Card card : gyCards) {
                graveyard.add(serializeCard(card, game, registry));
            }
            graveyard.sort(Comparator.<Map<String, Object>, String>comparing(m -> (String) m.get("name"))
                    .thenComparingInt(m -> ShortIdRegistry.parseSequence((String) m.get("id"))));
            p.put("graveyard", graveyard);

            // Exile
            List<Map<String, Object>> exile = new ArrayList<>();
            for (Card card : game.getExile().getCardsOwned(game, player.getId())) {
                exile.add(serializeCard(card, game, registry));
            }
            exile.sort(Comparator.<Map<String, Object>, String>comparing(m -> (String) m.get("name"))
                    .thenComparingInt(m -> ShortIdRegistry.parseSequence((String) m.get("id"))));
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
            if (so.getManaCost() != null) {
                si.put("mana_cost", so.getManaCost().getText());
            }
            // Targets
            if (so.getStackAbility() != null && so.getStackAbility().getTargets() != null) {
                List<Map<String, Object>> targetsList = new ArrayList<>();
                for (Target target : so.getStackAbility().getTargets()) {
                    for (UUID targetId : target.getTargets()) {
                        Map<String, Object> t = new LinkedHashMap<>();
                        t.put("id", registry.getOrAssign(targetId));
                        MageObject targetObj = game.getObject(targetId);
                        if (targetObj != null) {
                            t.put("name", targetObj.getName());
                        } else {
                            // Could be a player
                            Player targetPlayer = game.getPlayer(targetId);
                            if (targetPlayer != null) {
                                t.put("name", targetPlayer.getName());
                            }
                        }
                        targetsList.add(t);
                    }
                }
                if (!targetsList.isEmpty()) {
                    si.put("targets", targetsList);
                }
            }
            stack.add(si);
        }
        snapshot.put("stack", stack);

        // Combat
        if (state.getCombat() != null && !state.getCombat().getGroups().isEmpty()) {
            List<Map<String, Object>> combat = new ArrayList<>();
            for (CombatGroup group : state.getCombat().getGroups()) {
                Map<String, Object> g = new LinkedHashMap<>();
                List<Map<String, Object>> attackersList = new ArrayList<>();
                for (UUID aid : group.getAttackers()) {
                    Map<String, Object> a = new LinkedHashMap<>();
                    Permanent attacker = game.getPermanent(aid);
                    a.put("id", registry.getOrAssign(aid));
                    a.put("name", attacker != null ? attacker.getName() : "Unknown");
                    if (attacker != null) {
                        a.put("power", attacker.getPower().getValue());
                        a.put("toughness", attacker.getToughness().getValue());
                    }
                    attackersList.add(a);
                }
                g.put("attackers", attackersList);
                List<Map<String, Object>> blockersList = new ArrayList<>();
                for (UUID bid : group.getBlockers()) {
                    Map<String, Object> b = new LinkedHashMap<>();
                    Permanent blocker = game.getPermanent(bid);
                    b.put("id", registry.getOrAssign(bid));
                    b.put("name", blocker != null ? blocker.getName() : "Unknown");
                    if (blocker != null) {
                        b.put("power", blocker.getPower().getValue());
                        b.put("toughness", blocker.getToughness().getValue());
                    }
                    blockersList.add(b);
                }
                g.put("blockers", blockersList);
                // Defender
                UUID defenderId = group.getDefenderId();
                if (defenderId != null) {
                    Player defender = game.getPlayer(defenderId);
                    if (defender != null) {
                        g.put("defender", defender.getName());
                    }
                }
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
        // Phase tracking for phase_change events
        int lastTurn = -1;
        TurnPhase lastPhase = null;
        PhaseStep lastStep = null;

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
