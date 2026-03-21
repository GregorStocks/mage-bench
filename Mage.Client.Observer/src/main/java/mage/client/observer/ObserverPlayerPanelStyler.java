package mage.client.observer;

import mage.client.components.HoverButton;
import mage.client.components.MageRoundPane;
import mage.client.dialog.PreferencesDialog;
import mage.client.game.PlayAreaPanel;
import mage.client.game.PlayerPanelExt;
import mage.view.GameView;
import mage.view.PlayerView;
import org.apache.log4j.Logger;

import javax.swing.*;
import javax.swing.border.Border;
import java.awt.*;
import java.awt.event.MouseListener;
import java.lang.reflect.Field;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

final class ObserverPlayerPanelStyler {

    private static final Logger logger = Logger.getLogger(ObserverPlayerPanelStyler.class);

    private static final Color[] PLAYER_ACCENT_COLORS = {
            new Color(0x3b, 0x82, 0xf6),
            new Color(0xef, 0x44, 0x44),
            new Color(0x22, 0xc5, 0x5e),
            new Color(0xf5, 0x9e, 0x0b),
    };

    private static final Color[] PLAYER_BG_COLORS = {
            new Color(0x0c, 0x18, 0x38),
            new Color(0x28, 0x0c, 0x0c),
            new Color(0x0c, 0x24, 0x14),
            new Color(0x28, 0x1e, 0x06),
    };

    private final Map<UUID, Integer> playerColorIndices = new LinkedHashMap<>();
    private boolean playerPanelsStyled = false;

    void initializePlayerColors(GameView game) {
        if (!playerColorIndices.isEmpty() || game == null || game.getPlayers() == null) {
            return;
        }

        int idx = 0;
        for (PlayerView player : game.getPlayers()) {
            playerColorIndices.put(player.getPlayerId(), idx % PLAYER_ACCENT_COLORS.length);
            idx++;
        }
    }

    void stylePlayerPanels(Map<UUID, PlayAreaPanel> players) {
        if (playerPanelsStyled || playerColorIndices.isEmpty()) {
            return;
        }

        for (Map.Entry<UUID, Integer> entry : playerColorIndices.entrySet()) {
            UUID playerId = entry.getKey();
            int colorIdx = entry.getValue();
            PlayAreaPanel playArea = players.get(playerId);
            if (playArea == null) {
                continue;
            }

            Color bgTint = PLAYER_BG_COLORS[colorIdx];
            playArea.setOpaque(true);
            playArea.setBackground(bgTint);
        }

        playerPanelsStyled = true;
    }

    void updatePlayerHighlights(GameView game, Map<UUID, PlayAreaPanel> players) {
        if (game == null || game.getPlayers() == null || playerColorIndices.isEmpty()) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            UUID playerId = player.getPlayerId();
            PlayAreaPanel playArea = players.get(playerId);
            if (playArea == null) {
                continue;
            }

            Integer colorIdx = playerColorIndices.get(playerId);
            if (colorIdx == null) {
                continue;
            }

            Border border;
            if (player.isActive()) {
                Color accent = PLAYER_ACCENT_COLORS[colorIdx];
                border = BorderFactory.createCompoundBorder(
                        BorderFactory.createLineBorder(accent, 3),
                        BorderFactory.createEmptyBorder(1, 1, 1, 1)
                );
            } else {
                border = BorderFactory.createCompoundBorder(
                        BorderFactory.createLineBorder(new Color(0x44, 0x44, 0x44), 1),
                        BorderFactory.createEmptyBorder(3, 3, 3, 3)
                );
            }
            playArea.setBorder(border);

            overrideNameHighlight(playArea.getPlayerPanel(), player);
        }
    }

    void updatePlayerPanelVisibility(
            GameView game,
            Map<UUID, PlayAreaPanel> players,
            ObserverCostDisplay costDisplay
    ) {
        if (game == null || game.getPlayers() == null) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            PlayAreaPanel playArea = players.get(player.getPlayerId());
            if (playArea == null) {
                continue;
            }

            PlayerPanelExt playerPanel = playArea.getPlayerPanel();
            cleanupPlayerPanel(playerPanel);
            costDisplay.updateCostLabel(player, playerPanel);
        }
    }

    private void overrideNameHighlight(PlayerPanelExt playerPanel, PlayerView player) {
        try {
            Field btnField = PlayerPanelExt.class.getDeclaredField("btnPlayer");
            btnField.setAccessible(true);
            JButton btnPlayer = (JButton) btnField.get(playerPanel);

            Field avatarField = PlayerPanelExt.class.getDeclaredField("avatar");
            avatarField.setAccessible(true);
            HoverButton avatar = (HoverButton) avatarField.get(playerPanel);

            Field bgField = PlayerPanelExt.class.getDeclaredField("panelBackground");
            bgField.setAccessible(true);
            MageRoundPane panelBackground = (MageRoundPane) bgField.get(playerPanel);

            Border nameBorder;
            Color bgColor;
            if (player.hasPriority()) {
                nameBorder = BorderFactory.createLineBorder(Color.green, 3);
                bgColor = PreferencesDialog.getCurrentTheme().getPlayerPanel_activeBackgroundColor();
            } else if (player.hasLeft()) {
                nameBorder = BorderFactory.createLineBorder(Color.red, 2);
                bgColor = PreferencesDialog.getCurrentTheme().getPlayerPanel_deadBackgroundColor();
            } else {
                nameBorder = BorderFactory.createEmptyBorder(0, 0, 0, 0);
                bgColor = PreferencesDialog.getCurrentTheme().getPlayerPanel_inactiveBackgroundColor();
            }

            if (btnPlayer != null) {
                btnPlayer.setBorder(nameBorder);
            }
            if (avatar != null) {
                avatar.setBorder(nameBorder);
            }
            if (panelBackground != null) {
                panelBackground.setBackgroundColor(bgColor);
            }
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to override name highlight", e);
        }
    }

    private void cleanupPlayerPanel(PlayerPanelExt playerPanel) {
        try {
            setFieldsVisible(playerPanel, false, "manaLabels", "manaButtons");
            setComponentVisible(playerPanel, "life", false);
            setComponentVisible(playerPanel, "lifeLabel", false);
            setComponentVisible(playerPanel, "hand", false);
            setComponentVisible(playerPanel, "handLabel", false);
            setComponentVisible(playerPanel, "grave", false);
            setComponentVisible(playerPanel, "graveLabel", false);
            setComponentVisible(playerPanel, "exileZone", false);
            setComponentVisible(playerPanel, "exileLabel", false);
            setComponentVisible(playerPanel, "zonesPanel", false);

            setCounterVisibleIfNonZero(playerPanel, "poison", "poisonLabel");
            setCounterVisibleIfNonZero(playerPanel, "energy", "energyLabel");
            setCounterVisibleIfNonZero(playerPanel, "experience", "experienceLabel");
            setCounterVisibleIfNonZero(playerPanel, "rad", "radLabel");

            resizePlayerPanel(playerPanel);
            stripMouseListeners(playerPanel, "avatar");
            stripMouseListeners(playerPanel, "btnPlayer");
        } catch (Exception e) {
            logger.warn("Failed to cleanup player panel via reflection", e);
        }
    }

    private void stripMouseListeners(PlayerPanelExt playerPanel, String fieldName) {
        try {
            Field field = PlayerPanelExt.class.getDeclaredField(fieldName);
            field.setAccessible(true);
            Component comp = (Component) field.get(playerPanel);
            if (comp != null) {
                for (MouseListener ml : comp.getMouseListeners()) {
                    comp.removeMouseListener(ml);
                }
            }
        } catch (Exception ignored) {
        }
    }

    private void resizePlayerPanel(PlayerPanelExt playerPanel) {
        try {
            int avatarSize = ObserverUiScale.computeAvatarSize(playerPanel);
            int panelWidth = avatarSize + 14;
            int panelHeight = avatarSize + 40;

            Field bgField = PlayerPanelExt.class.getDeclaredField("panelBackground");
            bgField.setAccessible(true);
            JComponent panelBackground = (JComponent) bgField.get(playerPanel);

            if (panelBackground != null) {
                var newSize = new Dimension(panelWidth, panelHeight);
                panelBackground.setPreferredSize(newSize);
                panelBackground.setMaximumSize(newSize);
                panelBackground.revalidate();
            }

            var newSize = new Dimension(panelWidth, panelHeight + 5);
            playerPanel.setPreferredSize(newSize);
            playerPanel.setMaximumSize(newSize);
            playerPanel.revalidate();
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to resize player panel", e);
        }
    }

    private void setCounterVisibleIfNonZero(PlayerPanelExt playerPanel, String iconField, String labelField) {
        try {
            Field labelF = PlayerPanelExt.class.getDeclaredField(labelField);
            labelF.setAccessible(true);
            JLabel label = (JLabel) labelF.get(playerPanel);

            boolean visible = false;
            if (label != null) {
                String text = label.getText();
                if (text != null && !text.isEmpty()) {
                    try {
                        visible = Integer.parseInt(text) > 0;
                    } catch (NumberFormatException ignored) {
                    }
                }
            }

            setComponentVisible(playerPanel, iconField, visible);
            if (label != null) {
                label.setVisible(visible);
            }
        } catch (NoSuchFieldException | IllegalAccessException ignored) {
        }
    }

    private void setComponentVisible(PlayerPanelExt playerPanel, String fieldName, boolean visible) {
        try {
            Field field = PlayerPanelExt.class.getDeclaredField(fieldName);
            field.setAccessible(true);
            Component component = (Component) field.get(playerPanel);
            if (component != null) {
                component.setVisible(visible);
            }
        } catch (NoSuchFieldException | IllegalAccessException ignored) {
        }
    }

    private void setFieldsVisible(PlayerPanelExt playerPanel, boolean visible, String... fieldNames) {
        for (String fieldName : fieldNames) {
            try {
                Field field = PlayerPanelExt.class.getDeclaredField(fieldName);
                field.setAccessible(true);
                Object value = field.get(playerPanel);
                if (value instanceof Map<?, ?> map) {
                    for (Object key : map.keySet()) {
                        if (key instanceof Component comp) {
                            comp.setVisible(visible);
                        }
                    }
                    for (Object val : map.values()) {
                        if (val instanceof Component comp) {
                            comp.setVisible(visible);
                        }
                    }
                }
            } catch (NoSuchFieldException | IllegalAccessException ignored) {
            }
        }
    }
}
