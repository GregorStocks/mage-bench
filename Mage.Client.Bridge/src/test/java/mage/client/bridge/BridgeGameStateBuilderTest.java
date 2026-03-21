package mage.client.bridge;

import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class BridgeGameStateBuilderTest {

    @Test
    void buildStateSignatureSortsMapKeysRecursively() {
        Map<String, Object> left = new LinkedHashMap<>();
        left.put("b", 2);
        left.put("a", Map.of("y", 2, "x", 1));
        left.put("c", List.of(Map.of("beta", 2, "alpha", 1), "tail"));

        Map<String, Object> right = new LinkedHashMap<>();
        right.put("c", List.of(Map.of("alpha", 1, "beta", 2), "tail"));
        right.put("a", Map.of("x", 1, "y", 2));
        right.put("b", 2);

        assertThat(BridgeGameStateBuilder.buildStateSignature(left))
            .isEqualTo(BridgeGameStateBuilder.buildStateSignature(right))
            .isEqualTo("{a:{x:1,y:2},b:2,c:[{alpha:1,beta:2},tail]}");
    }
}
