package mage.client.bridge.processor;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class ManaColorChooserTest {

    private static final String PAY_UB = "Pay {1}{U}{B}<div style='font-size:11pt'><font color='#DAA520'>Satoru Umezawa</font> [10e]</div>";

    @Test
    void neededColorsComeFromTheCostOnly() {
        assertEquals(List.of("Blue", "Black"), ManaColorChooser.neededColors(PAY_UB));
        assertEquals(List.of("White", "Blue"), ManaColorChooser.neededColors("Pay {W/U}{W}"));
        assertEquals(List.of(), ManaColorChooser.neededColors("Pay {2}"));
        assertEquals(List.of(), ManaColorChooser.neededColors(null));
    }

    @Test
    void picksAStillNeededColorTheSourceOffers() {
        assertEquals("Blue", ManaColorChooser.pick(PAY_UB, Set.of("Blue", "Black")));
        assertEquals("Black", ManaColorChooser.pick("Pay {B}<div>x</div>", Set.of("Blue", "Black")));
        // Only the offered colours count: paying {G} with a UB tower is not our call.
        assertNull(ManaColorChooser.pick("Pay {G}<div>x</div>", Set.of("Blue", "Black")));
    }

    @Test
    void genericOnlyOrSingleOfferedIsAnswered() {
        assertEquals(true, Set.of("Blue", "Black").contains(ManaColorChooser.pick("Pay {2}<div>x</div>", Set.of("Blue", "Black"))));
        assertEquals("Red", ManaColorChooser.pick(null, Set.of("Red")));
        assertNull(ManaColorChooser.pick(null, Set.of("Red", "Green")));
        assertNull(ManaColorChooser.pick(PAY_UB, Set.of()));
    }

    @Test
    void prefersTheColorOtherSourcesCannotMake() {
        // Paying {U}{B} with a Tower first; a Swamp remains: take Blue, leave Black to the Swamp.
        Set<String> others = ManaColorChooser.producible(List.of("{T}: Add {B}."));
        assertEquals(Set.of("Black"), others);
        assertEquals("Blue", ManaColorChooser.pick(PAY_UB, Set.of("Blue", "Black"), others));
        // A dual and an any-colour rock remain: everything is coverable, so the first needed colour is fine.
        Set<String> rich = ManaColorChooser.producible(List.of("{T}: Add {U} or {B}.", "{T}: Add one mana of any color in your commander's color identity."));
        assertEquals(Set.of("White", "Blue", "Black", "Red", "Green"), rich);
        assertEquals("Blue", ManaColorChooser.pick(PAY_UB, Set.of("Blue", "Black"), rich));
        // Only an Island remains: take Black.
        assertEquals("Black", ManaColorChooser.pick(PAY_UB, Set.of("Blue", "Black"), ManaColorChooser.producible(List.of("{T}: Add {U}."))));
    }
}
