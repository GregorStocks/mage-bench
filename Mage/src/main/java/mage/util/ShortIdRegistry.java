package mage.util;

import java.util.Map;
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
        if (uuid == null || shortId == null) return;
        String existing = uuidToShort.putIfAbsent(uuid, shortId);
        if (existing == null) {
            shortToUuid.put(shortId, uuid);
            // Advance nextId past this registered ID to avoid conflicts
            try {
                int num = Integer.parseInt(shortId.substring(1));
                nextId.updateAndGet(current -> Math.max(current, num + 1));
            } catch (NumberFormatException e) {
                // Non-standard short ID format, ignore
            }
        }
    }

    /** Reset all mappings (call on game start). */
    public void clear() {
        uuidToShort.clear();
        shortToUuid.clear();
        nextId.set(1);
    }
}
