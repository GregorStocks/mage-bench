package mage.client.observer;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import mage.client.game.PlayerPanelExt;
import mage.view.PlayerView;
import org.apache.log4j.Logger;

import javax.swing.*;
import java.awt.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class ObserverCostDisplay {

    private static final Logger logger = Logger.getLogger(ObserverCostDisplay.class);

    private final Map<UUID, JLabel> costLabels = new HashMap<>();
    private final Map<String, Double> playerCosts = new HashMap<>();
    private final Set<String> llmPlayerNames = new HashSet<>();
    private Timer costPollTimer;
    private Path gameDirPath;
    private boolean costPollingInitialized = false;

    void init(Path gameDirPath, String configJson) {
        if (costPollingInitialized) {
            return;
        }

        if (gameDirPath == null) {
            return;
        }
        costPollingInitialized = true;
        this.gameDirPath = gameDirPath;

        llmPlayerNames.addAll(parseLlmPlayerNames(configJson));

        if (llmPlayerNames.isEmpty()) {
            return;
        }

        logger.info("Cost polling enabled for LLM players: " + llmPlayerNames);

        costPollTimer = new Timer(2000, e -> pollCostFiles());
        costPollTimer.start();
    }

    void stop() {
        if (costPollTimer != null) {
            costPollTimer.stop();
        }
    }

    void updateCostLabel(PlayerView player, PlayerPanelExt playerPanel) {
        String playerName = player.getName();
        if (!llmPlayerNames.contains(playerName)) {
            return;
        }

        Double cost = playerCosts.get(playerName);
        if (cost == null) {
            return;
        }

        UUID playerId = player.getPlayerId();
        JLabel costLabel = costLabels.get(playerId);

        if (costLabel == null) {
            double scale = ObserverUiScale.computeScaleFactor(playerPanel);
            int costW = (int) (94 * scale);
            int costH = (int) (16 * scale);
            costLabel = new JLabel();
            costLabel.setHorizontalAlignment(SwingConstants.CENTER);
            costLabel.setForeground(new Color(0, 200, 0));
            costLabel.setFont(costLabel.getFont().deriveFont(Font.BOLD, (float) (11 * scale)));
            costLabel.setPreferredSize(new Dimension(costW, costH));
            costLabel.setMaximumSize(new Dimension(costW, costH));

            Container westPanel = playerPanel.getParent();
            if (westPanel instanceof JPanel) {
                westPanel.add(costLabel, 1);
                westPanel.revalidate();
                westPanel.repaint();
                costLabels.put(playerId, costLabel);
            }
        }

        costLabel.setText(formatCost(cost));
        costLabel.setVisible(true);
    }

    static Set<String> parseLlmPlayerNames(String configJson) {
        var llmPlayerNames = new HashSet<String>();
        if (configJson == null || configJson.isEmpty()) {
            return llmPlayerNames;
        }

        try {
            JsonObject root = JsonParser.parseString(configJson).getAsJsonObject();
            if (root.has("players")) {
                for (com.google.gson.JsonElement elem : root.getAsJsonArray("players")) {
                    JsonObject player = elem.getAsJsonObject();
                    String type = player.has("type") ? player.get("type").getAsString() : "";
                    if ("pilot".equals(type)) {
                        llmPlayerNames.add(player.get("name").getAsString());
                    }
                }
            }
        } catch (Exception e) {
            logger.warn("Failed to parse LLM players from config", e);
        }
        return llmPlayerNames;
    }

    static String formatCost(double costUsd) {
        return String.format("$%.4f", costUsd);
    }

    private void pollCostFiles() {
        if (gameDirPath == null) {
            return;
        }
        for (String username : llmPlayerNames) {
            Path costFile = gameDirPath.resolve(username + "_cost.json");
            try {
                if (Files.exists(costFile)) {
                    var content = new String(Files.readAllBytes(costFile));
                    JsonObject data = JsonParser.parseString(content).getAsJsonObject();
                    double cost = data.get("cost_usd").getAsDouble();
                    playerCosts.put(username, cost);
                }
            } catch (Exception ignored) {
                // Cost files are written asynchronously by another process; retry on the next poll
                // if we catch a partial write or transient parse/read error.
            }
        }
    }
}
