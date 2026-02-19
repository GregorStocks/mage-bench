package mage.util;

import java.security.SecureRandom;
import java.util.Random;

/**
 * Replaces UUID.randomUUID()'s internal SecureRandom with a seeded deterministic
 * generator. After calling install(), all UUID.randomUUID() calls produce
 * reproducible UUIDs from the given seed, making game state byte-identical
 * across JVM runs.
 *
 * Used by golden tests (skipInitShuffling mode) to ensure deterministic game
 * object IDs, which in turn makes HashMap iteration order deterministic.
 *
 * Must be called before any UUID.randomUUID() calls that affect game state.
 *
 * Requires: --add-opens=java.base/java.util=ALL-UNNAMED
 *
 * <h2>Yes, this uses sun.misc.Unsafe. Here's why we're keeping it:</h2>
 * <p>
 * In Java 17+, {@code static final} fields can't be written via {@code Field.set()},
 * {@code VarHandle}, or {@code MethodHandle} — Unsafe is the only way without bytecode
 * manipulation. We explored alternatives:
 * <ul>
 *   <li>A Java agent with ASM to strip {@code final} and use reflection — equally gross,
 *       adds a build-time dependency and a shade plugin for a test-only hack.</li>
 *   <li>A custom UUID factory replacing all ~87 {@code UUID.randomUUID()} call sites —
 *       violates code isolation (can't modify upstream XMage code).</li>
 * </ul>
 * The target field ({@code UUID$Holder.numberGenerator}) has been stable from JDK 8
 * through JDK 25. If a future JDK removes it, this fails fast with a clear exception.
 * </p>
 */
public final class DeterministicUUID {

    private DeterministicUUID() {}

    public static void install(long seed) {
        try {
            // Access UUID$Holder.numberGenerator (private static final SecureRandom)
            Class<?> holderClass = Class.forName("java.util.UUID$Holder");
            java.lang.reflect.Field field = holderClass.getDeclaredField("numberGenerator");

            // Use Unsafe to write the static final field (Field.set() rejects final fields in Java 17+)
            java.lang.reflect.Field unsafeField = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
            unsafeField.setAccessible(true);
            sun.misc.Unsafe unsafe = (sun.misc.Unsafe) unsafeField.get(null);

            Object base = unsafe.staticFieldBase(field);
            long offset = unsafe.staticFieldOffset(field);
            unsafe.putObject(base, offset, new SeededSecureRandom(seed));
        } catch (Exception e) {
            throw new RuntimeException(
                    "Failed to install deterministic UUID generator. "
                            + "Ensure --add-opens=java.base/java.util=ALL-UNNAMED is set.",
                    e);
        }
    }

    /**
     * A SecureRandom that delegates to a seeded java.util.Random,
     * producing a deterministic byte sequence.
     */
    private static class SeededSecureRandom extends SecureRandom {
        private final Random rng;

        SeededSecureRandom(long seed) {
            this.rng = new Random(seed);
        }

        @Override
        public void nextBytes(byte[] bytes) {
            rng.nextBytes(bytes);
        }

        @Override
        public byte[] generateSeed(int numBytes) {
            byte[] result = new byte[numBytes];
            rng.nextBytes(result);
            return result;
        }
    }
}
