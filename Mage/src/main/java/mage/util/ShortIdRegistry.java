package mage.util;

import java.util.Comparator;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.logging.Logger;
import java.util.stream.Collectors;

/**
 * Bidirectional mapping between XMage UUIDs and short, token-efficient IDs.
 *
 * <h3>Dual-namespace design</h3>
 * The server uses prefix {@code "p"} (the default). The bridge client uses
 * prefix {@code "l"} for locally-assigned fallback IDs (via {@link #getOrAssign}),
 * while server-assigned IDs (received via {@link #register}) keep whatever
 * prefix the server used. When a server ID replaces a local ID, the old local
 * ID is kept as a resolve-only alias in {@code shortToUuid} so that stale
 * references (e.g. from an LLM's mana plan) still resolve to the correct UUID.
 *
 * <h3>Deterministic ordering invariant</h3>
 * All code that sorts game objects for display or ID assignment MUST produce
 * a deterministic order. The canonical sort key is {@code (name, shortId sequence)}.
 * For initial ID assignment of not-yet-assigned objects, pre-sort by name to
 * ensure unique-name objects get deterministic IDs, then post-sort the
 * serialized output by {@code (name, shortId)} to fix same-name sub-ordering.
 * Never use UUID as a sort key.
 *
 * Thread-safe: uses ConcurrentHashMap and AtomicInteger for safe access from
 * game thread (query events) and network thread (response events).
 */
public class ShortIdRegistry {

    private static final Logger logger = Logger.getLogger(ShortIdRegistry.class.getName());

    private final String prefix;
    private final Map<UUID, String> uuidToShort = new ConcurrentHashMap<>();
    private final Map<String, UUID> shortToUuid = new ConcurrentHashMap<>();
    private final AtomicInteger nextId = new AtomicInteger(1);

    /** Create a registry with the default {@code "p"} prefix (used by the server). */
    public ShortIdRegistry() {
        this("p");
    }

    /** Create a registry with a custom prefix for {@link #getOrAssign} IDs. */
    public ShortIdRegistry(String prefix) {
        this.prefix = Objects.requireNonNull(prefix);
    }

    /**
     * Get the short ID for a UUID, assigning a new one if first encounter.
     */
    public String getOrAssign(UUID uuid) {
        String existing = uuidToShort.get(uuid);
        if (existing != null) {
            return existing;
        }
        String shortId = prefix + nextId.getAndIncrement();
        String race = uuidToShort.putIfAbsent(uuid, shortId);
        if (race != null) {
            return race;
        }
        shortToUuid.put(shortId, uuid);
        return shortId;
    }

    /**
     * Get the numeric part of the short ID for a UUID, or Integer.MAX_VALUE if not yet assigned.
     * Safe for use in comparators (no side effects).
     */
    public int getSequence(UUID uuid) {
        String existing = uuidToShort.get(uuid);
        if (existing == null) {
            return Integer.MAX_VALUE;
        }
        return Integer.parseInt(existing.substring(1));
    }

    /**
     * Resolve a short ID back to its UUID.
     * @throws IllegalArgumentException if the short ID is not known
     */
    public UUID resolve(String shortId) {
        UUID uuid = shortToUuid.get(shortId);
        if (uuid == null) {
            throw new IllegalArgumentException("Unknown short ID: " + shortId);
        }
        return uuid;
    }

    /**
     * Resolve a short ID back to its UUID, or null if not known.
     * Non-throwing variant of {@link #resolve(String)}.
     */
    public UUID tryResolve(String shortId) {
        return shortToUuid.get(shortId);
    }

    /**
     * Register an externally-assigned short ID for a UUID. Used by clients to sync
     * with server-assigned IDs (via CardView.getShortId()). The server's assignment
     * is authoritative — if the UUID was previously assigned a different (local) ID
     * via {@link #getOrAssign}, the mapping is updated to the server's ID.
     *
     * Also advances nextId past the registered ID to avoid future conflicts.
     */
    public void register(UUID uuid, String shortId) {
        Objects.requireNonNull(uuid, "uuid");
        Objects.requireNonNull(shortId, "shortId");

        String existingShort = uuidToShort.get(uuid);
        if (existingShort != null) {
            if (existingShort.equals(shortId)) {
                return; // Already correctly mapped
            }
            // Server-assigned ID takes precedence over locally-assigned ID.
            // This happens when a card is first seen in a zone not searched by
            // findCardViewById (e.g. lookedAt) and gets a local ID, then later
            // appears in a visible zone with its server-assigned ID.
            // Keep the old ID in shortToUuid as a resolve-only alias so stale
            // references (e.g. from an LLM's mana plan) still resolve correctly.
            uuidToShort.put(uuid, shortId);
            shortToUuid.put(shortId, uuid);
            advanceNextId(shortId);
            return;
        }

        UUID existingUuid = shortToUuid.get(shortId);
        if (existingUuid != null && !existingUuid.equals(uuid)) {
            // With namespace separation (local=l, server=p), this should only
            // happen if the server assigned the same ID to two different UUIDs,
            // which is a server bug. Log at ERROR so it's impossible to miss.
            logger.severe("Server short ID collision: " + shortId + " was mapped to "
                    + existingUuid + " but server now says it belongs to " + uuid
                    + " — evicting old mapping (likely a server bug)");
            uuidToShort.remove(existingUuid, shortId);
            shortToUuid.remove(shortId, existingUuid);
        }

        uuidToShort.put(uuid, shortId);
        shortToUuid.put(shortId, uuid);
        advanceNextId(shortId);
    }

    private void advanceNextId(String shortId) {
        try {
            int num = Integer.parseInt(shortId.substring(1));
            nextId.updateAndGet(current -> Math.max(current, num + 1));
        } catch (NumberFormatException e) {
            // Non-standard short ID format, ignore
        }
    }

    /**
     * Parse the numeric sequence from a short ID string (e.g., "p6" → 6, "l3" → 3).
     * Useful for comparators operating on already-serialized short ID strings.
     */
    public static int parseSequence(String shortId) {
        return Integer.parseInt(shortId.substring(1));
    }

    /** Current value of the next-ID counter (for diagnostics). */
    public int peekNextId() {
        return nextId.get();
    }

    /**
     * Return a snapshot of all assignments sorted by sequence, formatted as
     * {@code "p1=abcd1234, p2=ef567890, ..."} (UUID truncated to 8 chars).
     * Useful for logging when diagnosing nondeterministic ID assignment.
     */
    public String dumpAssignments() {
        return uuidToShort.entrySet().stream()
                .sorted(Comparator.comparingInt(e -> {
                    try {
                        return Integer.parseInt(e.getValue().substring(1));
                    } catch (NumberFormatException ex) {
                        return Integer.MAX_VALUE;
                    }
                }))
                .map(e -> e.getValue() + "=" + e.getKey().toString().substring(0, 8))
                .collect(Collectors.joining(", ", "[", "]"));
    }

    /** Reset all mappings (call on game start). */
    public void clear() {
        uuidToShort.clear();
        shortToUuid.clear();
        nextId.set(1);
    }
}
