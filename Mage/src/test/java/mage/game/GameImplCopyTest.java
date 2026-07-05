package mage.game;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class GameImplCopyTest {

    @Test
    public void simulationCopyDoesNotAdvanceSourceGameSeq() {
        FakeGame game = new FakeGame();
        game.setGameOptions(new GameOptions());
        assertEquals(1, game.nextGameSeq());

        Game simulation = game.createSimulationForAI();

        assertTrue(simulation.isSimulation());
        assertEquals(1, game.getGameSeq());
        assertEquals(1, simulation.getGameSeq());

        assertEquals(2, simulation.nextGameSeq());

        assertEquals(1, game.getGameSeq());
        assertEquals(2, simulation.getGameSeq());
        assertEquals(2, game.nextGameSeq());
    }
}
