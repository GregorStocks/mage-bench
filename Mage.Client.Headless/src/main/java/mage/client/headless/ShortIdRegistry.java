package mage.client.headless;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Bidirectional mapping between XMage UUIDs and short, token-efficient IDs
 * for the MCP interface. Short IDs use format "p1", "p2", etc.
 *
 * IDs are stable for the lifetime of a game — the same UUID always maps to
 * the same short ID, even as objects move between zones.
 */
public class ShortIdRegistry {

    private final Map<UUID, String> uuidToShort = new HashMap<>();
    private final Map<String, UUID> shortToUuid = new HashMap<>();
    private int nextId = 1;

    /**
     * Get the short ID for a UUID, assigning a new one if first encounter.
     */
    public String getOrAssign(UUID uuid) {
        String existing = uuidToShort.get(uuid);
        if (existing != null) {
            return existing;
        }
        String shortId = "p" + nextId++;
        uuidToShort.put(uuid, shortId);
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

    /** Reset all mappings (call on game start). */
    public void clear() {
        uuidToShort.clear();
        shortToUuid.clear();
        nextId = 1;
    }
}
