package mage.util;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Bidirectional mapping between XMage UUIDs and short, token-efficient IDs.
 * Short IDs use format "p1", "p2", etc.
 *
 * IDs are stable for the lifetime of a game — the same UUID always maps to
 * the same short ID, even as objects move between zones.
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

    private final Map<UUID, String> uuidToShort = new ConcurrentHashMap<>();
    private final Map<String, UUID> shortToUuid = new ConcurrentHashMap<>();
    private final AtomicInteger nextId = new AtomicInteger(1);

    /**
     * Get the short ID for a UUID, assigning a new one if first encounter.
     */
    public String getOrAssign(UUID uuid) {
        String existing = uuidToShort.get(uuid);
        if (existing != null) {
            return existing;
        }
        String shortId = "p" + nextId.getAndIncrement();
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
     * Register an externally-assigned short ID for a UUID. Used by clients to populate
     * from server-assigned IDs (via CardView.getShortId()). If the UUID already has a
     * short ID, this is a no-op. Also advances nextId past the registered ID to avoid
     * future conflicts.
     */
    public void register(UUID uuid, String shortId) {
        Objects.requireNonNull(uuid, "uuid");
        Objects.requireNonNull(shortId, "shortId");

        String existingShort = uuidToShort.get(uuid);
        if (existingShort != null) {
            if (!existingShort.equals(shortId)) {
                throw new IllegalStateException("UUID already mapped to different short ID: "
                        + uuid + " -> " + existingShort + ", got " + shortId);
            }
            return;
        }

        UUID existingUuid = shortToUuid.get(shortId);
        if (existingUuid != null && !existingUuid.equals(uuid)) {
            throw new IllegalStateException("Short ID already mapped to different UUID: "
                    + shortId + " -> " + existingUuid + ", got " + uuid);
        }

        String raceShort = uuidToShort.putIfAbsent(uuid, shortId);
        if (raceShort != null) {
            if (!raceShort.equals(shortId)) {
                throw new IllegalStateException("UUID mapped concurrently to different short ID: "
                        + uuid + " -> " + raceShort + ", got " + shortId);
            }
            return;
        }

        UUID raceUuid = shortToUuid.putIfAbsent(shortId, uuid);
        if (raceUuid != null && !raceUuid.equals(uuid)) {
            uuidToShort.remove(uuid, shortId);
            throw new IllegalStateException("Short ID mapped concurrently to different UUID: "
                    + shortId + " -> " + raceUuid + ", got " + uuid);
        }

        // Advance nextId past this registered ID to avoid conflicts
        try {
            int num = Integer.parseInt(shortId.substring(1));
            nextId.updateAndGet(current -> Math.max(current, num + 1));
        } catch (NumberFormatException e) {
            // Non-standard short ID format, ignore
        }
    }

    /**
     * Parse the numeric sequence from a short ID string (e.g., "p6" → 6).
     * Useful for comparators operating on already-serialized short ID strings.
     */
    public static int parseSequence(String shortId) {
        return Integer.parseInt(shortId.substring(1));
    }

    /** Reset all mappings (call on game start). */
    public void clear() {
        uuidToShort.clear();
        shortToUuid.clear();
        nextId.set(1);
    }
}
