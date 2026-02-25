package mage.client.observer;

import mage.client.MagePane;

import javax.swing.*;
import java.awt.AWTEvent;
import java.nio.file.Path;
import java.util.UUID;

/**
 * observer-optimized game pane that uses ObserverGamePanel.
 * Based on mage.client.game.GamePane but uses the observer panel.
 */
public class ObserverGamePane extends MagePane {

    private ObserverGamePanel gamePanel;
    private JScrollPane jScrollPane1;
    private UUID currentTableId;
    private UUID parentTableId;
    private UUID gameId;

    public ObserverGamePane() {
        initComponents();
        SwingUtilities.invokeLater(() -> {
            gamePanel.setJLayeredPane(this);
            gamePanel.installComponents();
        });
    }

    private void initComponents() {
        jScrollPane1 = new JScrollPane();
        jScrollPane1.setBorder(BorderFactory.createEmptyBorder());
        gamePanel = new ObserverGamePanel();

        jScrollPane1.setViewportView(gamePanel);

        GroupLayout layout = new GroupLayout(this);
        this.setLayout(layout);
        layout.setHorizontalGroup(
                layout.createParallelGroup(GroupLayout.Alignment.LEADING)
                        .addComponent(jScrollPane1, GroupLayout.DEFAULT_SIZE, 600, Short.MAX_VALUE)
                        .addGap(0, 600, Short.MAX_VALUE)
        );
        layout.setVerticalGroup(
                layout.createParallelGroup(GroupLayout.Alignment.LEADING)
                        .addComponent(jScrollPane1, GroupLayout.Alignment.TRAILING, GroupLayout.DEFAULT_SIZE, 400, Short.MAX_VALUE)
                        .addGap(0, 400, Short.MAX_VALUE)
        );
    }

    public void showGame(UUID currentTableId, UUID parentTableId, UUID gameId, UUID playerId) {
        this.setTitle("Game " + gameId);
        this.currentTableId = currentTableId;
        this.parentTableId = parentTableId;
        this.gameId = gameId;
        gamePanel.showGame(currentTableId, parentTableId, gameId, playerId, this);
    }

    public void watchGame(UUID currentTableId, UUID parentTableId, UUID gameId) {
        this.setTitle("Observing " + gameId);
        this.currentTableId = currentTableId;
        this.parentTableId = parentTableId;
        this.gameId = gameId;
        gamePanel.watchGame(currentTableId, parentTableId, gameId, this);
    }

    public void replayGame(UUID gameId) {
        this.setTitle("Replaying " + gameId);
        this.currentTableId = null;
        this.parentTableId = null;
        this.gameId = gameId;
        gamePanel.replayGame(gameId);
    }

    public UUID getGameId() {
        return gameId;
    }

    @Override
    public boolean isActiveTable() {
        return this.gameId != null;
    }

    public void cleanUp() {
        // Stop recording before cleanup
        gamePanel.stopRecording();
        gamePanel.cleanUp();
    }

    /**
     * Start recording the game to a video file.
     */
    public void startRecording(Path outputPath) {
        gamePanel.startRecording(outputPath);
    }

    /**
     * Stop recording if in progress.
     */
    public void stopRecording() {
        gamePanel.stopRecording();
    }

    /**
     * Check if recording is active.
     */
    public boolean isRecording() {
        return gamePanel.isRecording();
    }

    @Override
    public void changeGUISize() {
        super.changeGUISize();
        gamePanel.changeGUISize();
        this.revalidate();
        this.repaint();
    }

    public void removeGame() {
        this.cleanUp();
        this.removeFrame();
    }

    @Override
    public void deactivated() {
        super.deactivated();
        gamePanel.onDeactivated();
    }

    @Override
    public void activated() {
        gamePanel.onActivated();
    }

    @Override
    public void handleEvent(AWTEvent event) {
        gamePanel.handleEvent(event);
    }

    @Override
    public UUID getSortTableId() {
        return parentTableId != null ? parentTableId : currentTableId;
    }
}
