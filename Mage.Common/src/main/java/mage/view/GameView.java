package mage.view;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import mage.MageObject;
import mage.abilities.costs.Cost;
import mage.cards.Card;
import mage.constants.PhaseStep;
import mage.constants.TurnPhase;
import mage.constants.Zone;
import mage.designations.Designation;
import mage.game.ExileZone;
import mage.game.Game;
import mage.game.GameState;
import mage.game.combat.CombatGroup;
import mage.game.command.Dungeon;
import mage.game.command.Emblem;
import mage.game.command.Plane;
import mage.game.permanent.Permanent;
import mage.game.permanent.PermanentCard;
import mage.game.permanent.PermanentToken;
import mage.game.stack.Spell;
import mage.game.stack.StackAbility;
import mage.game.stack.StackObject;
import mage.players.PlayableObjectsList;
import mage.players.Player;
import mage.util.CardUtil;
import org.apache.log4j.Logger;

import java.io.Serializable;
import java.util.*;

/**
 * @author BetaSteward_at_googlemail.com, JayDi85
 */
public class GameView implements Serializable {

    private static final long serialVersionUID = 1L;

    private static final Logger LOGGER = Logger.getLogger(GameView.class);

    private final int priorityTime;
    private final int bufferTime;
    private final List<PlayerView> players = new ArrayList<>();
    private UUID myPlayerId = null; // null for watcher
    private final CardsView myHand = new CardsView();
    private final CardsView myHelperEmblems = new CardsView();
    private PlayableObjectsList canPlayObjects;
    private final Map<String, SimpleCardsView> opponentHands = new HashMap<>();
    private final Map<String, SimpleCardsView> watchedHands = new HashMap<>();
    private final CardsView stack = new CardsView();
    private final List<ExileView> exiles = new ArrayList<>();
    private final List<RevealedView> revealed = new ArrayList<>();
    private final List<LookedAtView> lookedAt = new ArrayList<>();
    private final List<RevealedView> companion = new ArrayList<>();
    private final List<CombatGroupView> combat = new ArrayList<>();
    private final TurnPhase phase;
    private final PhaseStep step;
    private final UUID activePlayerId;
    private String activePlayerName = "";
    private final String priorityPlayerName;
    private final int turn;
    private boolean special = false;
    private final boolean rollbackTurnsAllowed;

    // Server-side game event log sequence counter — flows from Game to clients for cross-referencing
    private int gameSeq;

    // Retained for assigning short IDs to late-populated views (watchedHands, opponentHands)
    private transient mage.util.ShortIdRegistry shortIdRegistry;

    // for debug only
    // TODO: implement and support in admin tools
    private int totalErrorsCount;
    private int totalEffectsCount;
    private int gameCycle;

    public GameView(GameState state, Game game, UUID createdForPlayerId, UUID watcherUserId) {
        Player createdForPlayer = null;
        this.priorityTime = game.getPriorityTime();
        this.bufferTime = game.getBufferTime();

        for (Player player : state.getPlayers().values()) {
            PlayerView playerView = new PlayerView(player, state, game, createdForPlayerId, watcherUserId);
            players.add(playerView);
            if (player.getId().equals(createdForPlayerId)) {
                createdForPlayer = player;
                this.myPlayerId = player.getId();
                this.myHand.putAll(new CardsView(game, player.getHand().getCards(game), createdForPlayerId));
                state.getHelperEmblems().stream()
                        .filter(emblem -> emblem.isControlledBy(player.getId()))
                        .forEach(emblem -> {
                            this.myHelperEmblems.put(emblem.getId(), new CardView(new EmblemView(emblem, game)));
                        });
            }
        }
        for (StackObject stackObject : state.getStack()) {
            if (stackObject instanceof Spell) {
                // Spell
                Spell spell = (Spell) stackObject;
                CardView spellView = new CardView(spell, game, CardUtil.canShowAsControlled(spell, createdForPlayerId));
                spellView.paid = spell.getSpellAbility().getManaCostsToPay().isPaid();
                stack.put(spell.getId(), spellView);
            } else if (stackObject instanceof StackAbility) {
                // Stack Ability
                MageObject object = game.getObject(stackObject.getSourceId());
                Card card = game.getCard(stackObject.getSourceId());
                if (card == null && (object instanceof PermanentCard)) {
                    card = ((PermanentCard) object).getCard();
                }
                if (card != null) {
                    if (object != null) {
                        if (object instanceof Permanent) {
                            boolean controlled = ((Permanent) object).getControllerId().equals(createdForPlayerId);
                            stack.put(stackObject.getId(), new StackAbilityView(game, (StackAbility) stackObject, object.getName(), object, new CardView(((Permanent) object), game, controlled, false)));
                        } else {
                            stack.put(stackObject.getId(), new StackAbilityView(game, (StackAbility) stackObject, card.getName(), card, new CardView(card, game, false, false)));
                        }
                    } else {
                        stack.put(stackObject.getId(), new StackAbilityView(game, (StackAbility) stackObject, "", card, new CardView(card, game)));
                    }
                    if (card.isTransformable()) {
                        updateLatestCardView(game, card, stackObject.getId());
                    }
                    checkPaid(stackObject.getId(), (StackAbility) stackObject);
                } else if (object != null) {
                    if (object instanceof PermanentToken) {
                        PermanentToken token = (PermanentToken) object;
                        stack.put(stackObject.getId(), new StackAbilityView(game, (StackAbility) stackObject, token.getName(), token, new CardView(token, game)));
                        checkPaid(stackObject.getId(), (StackAbility) stackObject);
                    } else if (object instanceof Emblem) {
                        CardView cardView = new CardView(new EmblemView((Emblem) object, game));
                        // Card sourceCard = (Card) ((Emblem) object).getSourceObject();
                        stackObject.setName(object.getName());
                        // ((StackAbility) stackObject).setExpansionSetCode(sourceCard.getExpansionSetCode());
                        stack.put(stackObject.getId(),
                                new StackAbilityView(game, (StackAbility) stackObject, object.getName(), object, cardView));
                        checkPaid(stackObject.getId(), ((StackAbility) stackObject));
                    } else if (object instanceof Dungeon) {
                        CardView cardView = new CardView(new DungeonView((Dungeon) object));
                        stackObject.setName(object.getName());
                        stack.put(stackObject.getId(),
                                new StackAbilityView(game, (StackAbility) stackObject, object.getName(), object, cardView));
                        checkPaid(stackObject.getId(), ((StackAbility) stackObject));
                    } else if (object instanceof Plane) {
                        CardView cardView = new CardView(new PlaneView((Plane) object, game));
                        stackObject.setName(object.getName());
                        stack.put(stackObject.getId(),
                                new StackAbilityView(game, (StackAbility) stackObject, object.getName(), object, cardView));
                        checkPaid(stackObject.getId(), ((StackAbility) stackObject));
                    } else if (object instanceof Designation) {
                        Designation designation = (Designation) game.getObject(object.getId());
                        if (designation != null) {
                            stack.put(stackObject.getId(), new StackAbilityView(game, (StackAbility) stackObject, designation.getName(), designation, new CardView(designation, (StackAbility) stackObject)));
                        } else {
                            throw new IllegalArgumentException("Designation object not found: " + object + " - " + object.getClass().toString());
                        }
                    } else if (object instanceof StackAbility) {
                        StackAbility stackAbility = ((StackAbility) object);
                        stackAbility.newId();
                        stack.put(stackObject.getId(), new CardView(stackObject, game));
                        checkPaid(stackObject.getId(), ((StackAbility) stackObject));
                    } else {
                        throw new IllegalArgumentException("Object can't be cast to StackAbility: " + object + " - " + object.getClass().toString());
                    }
                } else {
                    // can happen if a player times out while ability is on the stack
                    LOGGER.debug("Stack Object for stack ability not found: " + stackObject.getStackAbility().getRule());
                }
            } else if (stackObject != null) {
                throw new IllegalArgumentException("Unknown type of StackObject: " + stackObject + " - " + stackObject.getClass().toString());
            }
        }

        for (ExileZone exileZone : state.getExile().getExileZones()) {
            exiles.add(new ExileView(exileZone, game, createdForPlayerId));
        }
        for (String name : state.getRevealed().keySet()) {
            revealed.add(new RevealedView(name, state.getRevealed().get(name), game));
        }
        if (this.myPlayerId != null) {
            for (String name : state.getLookedAt(this.myPlayerId).keySet()){
                lookedAt.add(new LookedAtView(name, state.getLookedAt(this.myPlayerId).get(name), game));
            }
        }
        for (String name : state.getCompanion().keySet()) {
            // Only show the companion window when the companion is still outside the game.
            if (state.getCompanion().get(name).stream().anyMatch(cardId -> state.getZone(cardId) == Zone.OUTSIDE)) {
                companion.add(new RevealedView(name, state.getCompanion().get(name), game));
            }
        }
        this.phase = state.getTurnPhaseType();
        this.step = state.getTurnStepType();
        this.turn = state.getTurnNum();
        this.activePlayerId = state.getActivePlayerId();
        if (state.getActivePlayerId() != null) {
            this.activePlayerName = state.getPlayer(state.getActivePlayerId()).getName();
        } else {
            this.activePlayerName = "";
        }
        Player priorityPlayer = null;
        if (state.getPriorityPlayerId() != null) {
            priorityPlayer = state.getPlayer(state.getPriorityPlayerId());
            this.priorityPlayerName = priorityPlayer != null ? priorityPlayer.getName() : "";
        } else {
            this.priorityPlayerName = "";
        }
        for (CombatGroup combatGroup : state.getCombat().getGroups()) {
            combat.add(new CombatGroupView(combatGroup, game));
        }
        if (this.myPlayerId != null) { // no watcher
            // has only to be set for active player with priority (e.g. pay mana by delve or Quenchable Fire special action)
            if (priorityPlayer != null && createdForPlayer != null && createdForPlayer.isGameUnderControl()
                    && (createdForPlayerId.equals(priorityPlayer.getId()) // player controls the turn
                    || createdForPlayer.getPlayersUnderYourControl().contains(priorityPlayer.getId()))) { // player controls active players turn
                this.special = !state.getSpecialActions().getControlledBy(priorityPlayer.getId(), priorityPlayer.isInPayManaMode()).isEmpty();
            }
        } else {
            this.special = false;
        }
        this.rollbackTurnsAllowed = game.getOptions().rollbackTurnsAllowed;
        this.gameSeq = game.getGameSeq();
        this.totalErrorsCount = game.getTotalErrorsCount();
        this.totalEffectsCount = game.getTotalEffectsCount();
        this.gameCycle = game.getState().getApplyEffectsCounter();

        // Assign short IDs from the server's ShortIdRegistry to all card views
        assignShortIds(game);
    }

    private void assignShortIds(Game game) {
        mage.util.ShortIdRegistry registry = game.getShortIdRegistry();
        this.shortIdRegistry = registry;
        int idAtStart = registry.peekNextId();
        StringBuilder traceLog = new StringBuilder();

        // Assign IDs in deterministic order: name, then shortId sequence.
        // See ShortIdRegistry for the deterministic ordering invariant.
        Comparator<CardView> byName = Comparator.comparing(
            (CardView cv) -> cv.getDisplayName() != null ? cv.getDisplayName() : "",
            String::compareTo
        ).thenComparingInt(cv -> registry.getSequence(cv.getId()));

        // Helper to assign shortId to a CardsView (sorted by display name).
        // When tracing, logs each NEW assignment as "shortId=name".
        java.util.function.BiConsumer<String, CardsView> assignCards = (String section, CardsView cards) -> {
            List<CardView> sorted = new ArrayList<>(cards.values());
            sorted.sort(byName);
            for (CardView cv : sorted) {
                boolean isNew = registry.getSequence(cv.getId()) == Integer.MAX_VALUE;
                cv.setShortId(registry.getOrAssign(cv.getId()));
                if (isNew) {
                    traceLog.append("  ").append(section).append(": ")
                            .append(cv.getShortId()).append("=").append(cv.getDisplayName())
                            .append('\n');
                }
            }
        };

        // Player views — battlefield, graveyard, exile, topCard, commanders
        // Sort by name for deterministic player processing order
        List<PlayerView> sortedPlayers = new ArrayList<>(players);
        sortedPlayers.sort(Comparator.comparing(PlayerView::getName));
        for (PlayerView pv : sortedPlayers) {
            String playerPrefix = pv.getName() + "/";
            List<PermanentView> sortedBf = new ArrayList<>(pv.getBattlefield().values());
            sortedBf.sort(byName);
            for (PermanentView permView : sortedBf) {
                boolean isNew = registry.getSequence(permView.getId()) == Integer.MAX_VALUE;
                permView.setShortId(registry.getOrAssign(permView.getId()));
                if (isNew) {
                    traceLog.append("  ").append(playerPrefix).append("battlefield: ")
                            .append(permView.getShortId()).append("=").append(permView.getDisplayName())
                            .append('\n');
                }
            }
            assignCards.accept(playerPrefix + "graveyard", pv.getGraveyard());
            assignCards.accept(playerPrefix + "exile", pv.getExile());
            // Top card of library (when revealed)
            CardView topCard = pv.getTopCard();
            if (topCard != null) {
                boolean isNew = registry.getSequence(topCard.getId()) == Integer.MAX_VALUE;
                topCard.setShortId(registry.getOrAssign(topCard.getId()));
                if (isNew) {
                    traceLog.append("  ").append(playerPrefix).append("topCard: ")
                            .append(topCard.getShortId()).append("=").append(topCard.getDisplayName())
                            .append('\n');
                }
            }
            // Commanders (CommanderView extends CardView, others don't)
            if (pv.getCommandObjectList() != null) {
                for (CommandObjectView cmd : pv.getCommandObjectList()) {
                    if (cmd instanceof CommanderView) {
                        CommanderView cv = (CommanderView) cmd;
                        boolean isNew = registry.getSequence(cv.getId()) == Integer.MAX_VALUE;
                        cv.setShortId(registry.getOrAssign(cv.getId()));
                        if (isNew) {
                            traceLog.append("  ").append(playerPrefix).append("commander: ")
                                    .append(cv.getShortId()).append("=").append(cv.getDisplayName())
                                    .append('\n');
                        }
                    }
                }
            }
        }

        // Pre-assign IDs to ALL players' hand cards in deterministic order.
        // Each GameView only has myHand (the controlling player's hand), but
        // GameViews for different players share the same ShortIdRegistry and are
        // created in nondeterministic order (ConcurrentHashMap iteration in
        // GameController). Pre-assigning from the Game object ensures all hands
        // get IDs in sorted-by-player-name order regardless of creation order.
        for (PlayerView pv : sortedPlayers) {
            Player gamePlayer = game.getPlayer(pv.getPlayerId());
            if (gamePlayer != null) {
                List<Card> handCards = new ArrayList<>(gamePlayer.getHand().getCards(game));
                handCards.sort(Comparator.comparing(Card::getName));
                for (Card card : handCards) {
                    boolean isNew = registry.getSequence(card.getId()) == Integer.MAX_VALUE;
                    registry.getOrAssign(card.getId());
                    if (isNew) {
                        traceLog.append("  ").append(pv.getName()).append("/hand-preassign: ")
                                .append(registry.getOrAssign(card.getId())).append("=").append(card.getName())
                                .append('\n');
                    }
                }
            }
        }

        // Hands (myHand for the controlling player)
        assignCards.accept("myHand", myHand);
        // Note: watchedHands/opponentHands are populated later by processWatchedHands;
        // call assignShortIdsToHands() after populating them.

        // Stack
        assignCards.accept("stack", stack);

        // Combat (attackers/blockers are separate PermanentView instances, same UUIDs as battlefield)
        for (CombatGroupView cg : combat) {
            assignCards.accept("combat/attackers", cg.getAttackers());
            assignCards.accept("combat/blockers", cg.getBlockers());
        }

        // Exile zones (game-level, overlaps with per-player exile)
        for (ExileView ev : exiles) {
            assignCards.accept("exile/" + ev.getName(), ev);
        }

        // Revealed, companion, lookedAt
        for (RevealedView rv : revealed) {
            assignCards.accept("revealed/" + rv.getName(), rv.getCards());
        }
        for (RevealedView rv : companion) {
            assignCards.accept("companion/" + rv.getName(), rv.getCards());
        }
        for (LookedAtView lv : lookedAt) {
            for (SimpleCardView sv : lv.getCards().values()) {
                boolean isNew = registry.getSequence(sv.getId()) == Integer.MAX_VALUE;
                sv.setShortId(registry.getOrAssign(sv.getId()));
                if (isNew) {
                    traceLog.append("  lookedAt/").append(lv.getName()).append(": ")
                            .append(sv.getShortId()).append("=").append(sv.getExpansionSetCode())
                            .append('#').append(sv.getCardNumber()).append('\n');
                }
            }
        }

        // Helper emblems
        assignCards.accept("helperEmblems", myHelperEmblems);

        // Assign short IDs to players themselves (for targeting).
        // Placed after all card/permanent assignments to avoid shifting existing card IDs.
        for (PlayerView pv : sortedPlayers) {
            boolean isNew = registry.getSequence(pv.getPlayerId()) == Integer.MAX_VALUE;
            pv.setShortId(registry.getOrAssign(pv.getPlayerId()));
            if (isNew) {
                traceLog.append("  player: ").append(pv.getShortId()).append("=").append(pv.getName())
                        .append('\n');
            }
        }

        int newCount = registry.peekNextId() - idAtStart;
        if (newCount > 0) {
            String forLabel = myPlayerId != null ? String.valueOf(myPlayerId).substring(0, 8) : "watcher";
            LOGGER.info("assignShortIds[for=" + forLabel
                    + ", nextId=" + idAtStart + "->" + registry.peekNextId() + "] " + newCount + " new:\n" + traceLog);
        }
    }

    /**
     * Assign short IDs to watchedHands and opponentHands.
     * Must be called after processWatchedHands() populates these maps,
     * since they are empty at GameView construction time.
     */
    public void assignShortIdsToHands() {
        if (shortIdRegistry == null) {
            return;
        }
        for (SimpleCardsView scv : watchedHands.values()) {
            for (SimpleCardView sv : scv.values()) {
                sv.setShortId(shortIdRegistry.getOrAssign(sv.getId()));
            }
        }
        for (SimpleCardsView scv : opponentHands.values()) {
            for (SimpleCardView sv : scv.values()) {
                sv.setShortId(shortIdRegistry.getOrAssign(sv.getId()));
            }
        }
    }

    private void checkPaid(UUID uuid, StackAbility stackAbility) {
        for (Cost cost : stackAbility.getManaCostsToPay()) {
            if (!cost.isPaid()) {
                return;
            }
        }
        CardView cardView = stack.get(uuid);
        cardView.paid = true;
    }

    private void updateLatestCardView(Game game, Card card, UUID stackId) {
        if (!card.isTransformable()) {
            return;
        }
        Permanent permanent = game.getPermanent(card.getId());
        if (permanent == null) {
            permanent = (Permanent) game.getLastKnownInformation(card.getId(), Zone.BATTLEFIELD);
        }
        if (permanent != null) {
            if (permanent.isTransformed()) {
                StackAbilityView stackAbilityView = (StackAbilityView) stack.get(stackId);
                stackAbilityView.getSourceCard().setTransformed(true);
            }
        }
    }

    public List<PlayerView> getPlayers() {
        return players;
    }

    public CardsView getMyHand() {
        return myHand;
    }

    public CardsView getMyHelperEmblems() {
        return myHelperEmblems;
    }

    public PlayerView getMyPlayer() {
        if (this.myPlayerId == null) {
            return null;
        } else {
            return players.stream().filter(p -> p.getPlayerId().equals(this.myPlayerId)).findFirst().orElse(null);
        }
    }

    public Map<String, SimpleCardsView> getOpponentHands() {
        return opponentHands;
    }

    public Map<String, SimpleCardsView> getWatchedHands() {
        return watchedHands;
    }

    public TurnPhase getPhase() {
        return phase;
    }

    public PhaseStep getStep() {
        return step;
    }

    public CardsView getStack() {
        return stack;
    }

    public List<ExileView> getExile() {
        return exiles;
    }

    public List<RevealedView> getRevealed() {
        return revealed;
    }

    public List<LookedAtView> getLookedAt() {
        return lookedAt;
    }

    public List<RevealedView> getCompanion() {
        return companion;
    }

    public List<CombatGroupView> getCombat() {
        return combat;
    }

    public int getTurn() {
        return this.turn;
    }

    public String getActivePlayerName() {
        return activePlayerName;
    }

    public String getPriorityPlayerName() {
        return priorityPlayerName;
    }

    public boolean getSpecial() {
        return special;
    }

    public int getPriorityTime() {
        return priorityTime;
    }

    public int getBufferTime() {
        return bufferTime;
    }

    public UUID getActivePlayerId() {
        return activePlayerId;
    }

    public boolean isPlayer() {
        return this.myPlayerId != null;
    }

    public PlayableObjectsList getCanPlayObjects() {
        return canPlayObjects;
    }

    public void setCanPlayObjects(PlayableObjectsList canPlayObjects) {
        this.canPlayObjects = canPlayObjects;
    }

    public boolean isRollbackTurnsAllowed() {
        return rollbackTurnsAllowed;
    }

    public String toJson() {
        Gson gson = new GsonBuilder().create();
        return gson.toJson(this);
    }

    public int getTotalErrorsCount() {
        return this.totalErrorsCount;
    }

    public int getTotalEffectsCount() {
        return this.totalEffectsCount;
    }

    public int getGameCycle() {
        return this.gameCycle;
    }

    public int getGameSeq() {
        return this.gameSeq;
    }
}
