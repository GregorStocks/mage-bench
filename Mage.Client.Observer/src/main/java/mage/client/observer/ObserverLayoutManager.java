package mage.client.observer;

import mage.cards.MageCard;
import mage.client.chat.ChatPanelBasic;
import mage.client.dialog.MageDialog;
import mage.client.dialog.PreferencesDialog;
import mage.client.game.GamePanel;
import mage.client.game.PlayAreaPanel;
import mage.client.plugins.impl.Plugins;
import mage.client.util.GUISizeHelper;
import org.apache.log4j.Logger;

import javax.swing.*;
import javax.swing.event.HyperlinkListener;
import java.awt.*;
import java.awt.event.MouseListener;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class ObserverLayoutManager {

    private static final Logger logger = Logger.getLogger(ObserverLayoutManager.class);

    private final Set<String> scheduledDismissals = new HashSet<>();
    private boolean handContainerHidden = false;

    void stripChatHoverEffects(ChatPanelBasic chatPanel) {
        try {
            Field txtField = ChatPanelBasic.class.getDeclaredField("txtConversation");
            txtField.setAccessible(true);
            JEditorPane textPane = (JEditorPane) txtField.get(chatPanel);
            if (textPane != null) {
                for (HyperlinkListener hl : textPane.getHyperlinkListeners()) {
                    textPane.removeHyperlinkListener(hl);
                }
                for (MouseListener ml : textPane.getMouseListeners()) {
                    textPane.removeMouseListener(ml);
                }
            }
        } catch (Exception e) {
            logger.warn("Failed to strip chat hover effects", e);
        }
    }

    void restoreDeadPlayerPanelSizes(Map<UUID, PlayAreaPanel> players) {
        var parentsToRevalidate = new HashSet<Container>();
        for (PlayAreaPanel playArea : players.values()) {
            Container parent = playArea.getParent();
            if (parent == null || !(parent.getLayout() instanceof GridBagLayout)) {
                continue;
            }
            GridBagLayout layout = (GridBagLayout) parent.getLayout();
            GridBagConstraints gbc = layout.getConstraints(playArea);
            if (Math.abs(gbc.weightx - 0.5) > 0.01) {
                gbc.weightx = 0.5;
                layout.setConstraints(playArea, gbc);
                parentsToRevalidate.add(parent);
            }
            playArea.setPreferredSize(null);
        }
        for (Container parent : parentsToRevalidate) {
            parent.validate();
            parent.repaint();
        }
    }

    void hideHandContainer(ObserverGamePanel panel) {
        if (handContainerHidden) {
            return;
        }

        try {
            Field helperAreaField = GamePanel.class.getDeclaredField("pnlHelperHandButtonsStackArea");
            helperAreaField.setAccessible(true);
            JPanel helperArea = (JPanel) helperAreaField.get(panel);

            if (helperArea != null && helperArea.getLayout() instanceof BorderLayout layout) {
                Component southComponent = layout.getLayoutComponent(BorderLayout.SOUTH);
                if (southComponent != null) {
                    southComponent.setVisible(false);
                    helperArea.remove(southComponent);
                }

                reparentStackPanel(panel, helperArea);

                helperArea.revalidate();
                helperArea.repaint();
            }

            Field btnSwitchHandsField = GamePanel.class.getDeclaredField("btnSwitchHands");
            btnSwitchHandsField.setAccessible(true);
            JButton btnSwitchHands = (JButton) btnSwitchHandsField.get(panel);
            if (btnSwitchHands != null) {
                btnSwitchHands.setVisible(false);
            }

            handContainerHidden = true;
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to hide hand container via reflection", e);
        }

        hideBigCardPanel(panel);
    }

    void relayoutStackVertically(ObserverGamePanel panel) {
        try {
            Field stackField = GamePanel.class.getDeclaredField("stackObjects");
            stackField.setAccessible(true);
            mage.client.cards.Cards stackCards = (mage.client.cards.Cards) stackField.get(panel);
            if (stackCards == null) {
                return;
            }

            Field cardAreaField = mage.client.cards.Cards.class.getDeclaredField("cardArea");
            cardAreaField.setAccessible(true);
            JPanel cardArea = (JPanel) cardAreaField.get(stackCards);
            if (cardArea == null) {
                return;
            }

            var cardsToLayout = new ArrayList<MageCard>();
            for (Component c : cardArea.getComponents()) {
                if (c instanceof MageCard mc) {
                    cardsToLayout.add(mc);
                }
            }

            Dimension cardDim = GUISizeHelper.handCardDimension;
            int cardWidth = cardDim.width;
            int cardHeight = cardDim.height;
            int panelHeight = (int) (cardHeight * 1.5);

            if (cardsToLayout.isEmpty()) {
                stackCards.setPreferredSize(new Dimension(0, panelHeight));
                stackCards.revalidate();
                return;
            }
            int overlapGap = (int) (cardHeight * 0.4);
            int margin = 4;

            int dy = margin;
            for (MageCard card : cardsToLayout) {
                card.setCardLocation(margin, dy);
                dy += overlapGap;
            }
            int totalHeight = dy - overlapGap + cardHeight + margin;

            cardArea.setPreferredSize(new Dimension(cardWidth + margin * 2, totalHeight));
            cardArea.revalidate();

            stackCards.setPreferredSize(new Dimension(0, panelHeight));
            stackCards.revalidate();
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to re-layout stack vertically", e);
        }
    }

    void removeSplitterFromRestore(ObserverGamePanel panel) {
        try {
            Field splittersField = GamePanel.class.getDeclaredField("splitters");
            splittersField.setAccessible(true);
            @SuppressWarnings("unchecked")
            Map<String, ?> splitters = (Map<String, ?>) splittersField.get(panel);
            splitters.remove(PreferencesDialog.KEY_GAMEPANEL_DIVIDER_LOCATIONS_HAND_STACK);
            splitters.remove(PreferencesDialog.KEY_GAMEPANEL_DIVIDER_LOCATIONS_GAME_AND_BIG_CARD);
            splitters.remove(PreferencesDialog.KEY_GAMEPANEL_DIVIDER_LOCATIONS_CHAT_AND_LOGS);
            splitters.remove(PreferencesDialog.KEY_GAMEPANEL_DIVIDER_LOCATIONS_BATTLEFIELD_AND_CHATS);
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to remove splitters from restore", e);
        }
    }

    void adjustBattlefieldCardSizes(Component component) {
        double scale = ObserverUiScale.computeScaleFactor(component);
        int maxW = (int) (100 * scale);
        int maxH = (int) (maxW * GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);
        int minW = (int) (20 * scale);
        int minH = (int) (minW * GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);
        GUISizeHelper.battlefieldCardMaxDimension = new Dimension(maxW, maxH);
        GUISizeHelper.battlefieldCardMinDimension = new Dimension(minW, minH);
        int maxWidth = GUISizeHelper.battlefieldCardMaxDimension.width;
        if (GUISizeHelper.handCardDimension.width > maxWidth) {
            int maxHeight = (int) (maxWidth * GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);
            GUISizeHelper.handCardDimension = new Dimension(maxWidth, maxHeight);
        }
        Plugins.instance.changeGUISize();
    }

    void schedulePopupDismissal(ObserverGamePanel panel) {
        scheduleDismissalForMap(panel, "revealed");
        scheduleDismissalForMap(panel, "lookedAt");
        scheduleDismissalForMap(panel, "companion");
        scheduleDismissalForMap(panel, "graveyardWindows");
        scheduleDismissalForMap(panel, "sideboardWindows");
    }

    private void reparentStackPanel(ObserverGamePanel panel, JPanel helperArea) {
        try {
            Field stackField = GamePanel.class.getDeclaredField("stackObjects");
            stackField.setAccessible(true);
            mage.client.cards.Cards stackPanel = (mage.client.cards.Cards) stackField.get(panel);

            Field splitBFChatField = GamePanel.class.getDeclaredField("splitBattlefieldAndChats");
            splitBFChatField.setAccessible(true);
            JSplitPane splitBFChat = (JSplitPane) splitBFChatField.get(panel);

            Field splitChatLogsField = GamePanel.class.getDeclaredField("splitChatAndLogs");
            splitChatLogsField.setAccessible(true);
            JSplitPane splitChatLogs = (JSplitPane) splitChatLogsField.get(panel);

            if (stackPanel != null && splitBFChat != null && splitChatLogs != null) {
                Container oldParent = stackPanel.getParent();
                if (oldParent != null) {
                    oldParent.remove(stackPanel);
                }

                Field scrollField = mage.client.cards.Cards.class.getDeclaredField("jScrollPane1");
                scrollField.setAccessible(true);
                JScrollPane scrollPane = (JScrollPane) scrollField.get(stackPanel);
                if (scrollPane != null) {
                    scrollPane.setVerticalScrollBarPolicy(ScrollPaneConstants.VERTICAL_SCROLLBAR_AS_NEEDED);
                    scrollPane.setHorizontalScrollBarPolicy(ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
                }

                stackPanel.setVisible(true);

                var rightWrapper = new JPanel(new BorderLayout());
                rightWrapper.setOpaque(false);
                rightWrapper.add(stackPanel, BorderLayout.NORTH);
                rightWrapper.add(splitChatLogs, BorderLayout.CENTER);

                int minWidth = GUISizeHelper.handCardDimension.width + 30;
                rightWrapper.setMinimumSize(new Dimension(minWidth, 0));

                splitBFChat.setRightComponent(rightWrapper);

                SwingUtilities.invokeLater(() -> {
                    int totalWidth = splitBFChat.getWidth();
                    if (totalWidth > 0) {
                        splitBFChat.setDividerLocation(totalWidth - Math.max(minWidth, totalWidth / 5));
                    }
                });
            }
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to reparent stack panel", e);
        }
    }

    private void hideBigCardPanel(ObserverGamePanel panel) {
        try {
            Field bigCardPanelField = GamePanel.class.getDeclaredField("bigCardPanel");
            bigCardPanelField.setAccessible(true);
            JPanel bigCardPanel = (JPanel) bigCardPanelField.get(panel);
            if (bigCardPanel != null) {
                bigCardPanel.setVisible(false);
            }

            Field splitField = GamePanel.class.getDeclaredField("splitGameAndBigCard");
            splitField.setAccessible(true);
            JSplitPane splitPane = (JSplitPane) splitField.get(panel);
            if (splitPane != null) {
                splitPane.setDividerLocation(1.0);
                splitPane.setDividerSize(0);
            }
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to hide big card panel via reflection", e);
        }
    }

    @SuppressWarnings("unchecked")
    private void scheduleDismissalForMap(ObserverGamePanel panel, String fieldName) {
        try {
            Field field = GamePanel.class.getDeclaredField(fieldName);
            field.setAccessible(true);
            Map<String, ? extends MageDialog> map = (Map<String, ? extends MageDialog>) field.get(panel);

            for (Map.Entry<String, ? extends MageDialog> entry : map.entrySet()) {
                String key = fieldName + ":" + entry.getKey();
                if (scheduledDismissals.contains(key)) {
                    continue;
                }
                scheduledDismissals.add(key);

                MageDialog dialog = entry.getValue();
                String dialogKey = entry.getKey();
                var dismissTimer = new Timer(15000, e -> {
                    dialog.hideDialog();
                    try {
                        Field f = GamePanel.class.getDeclaredField(fieldName);
                        f.setAccessible(true);
                        Map<String, ?> m = (Map<String, ?>) f.get(panel);
                        m.remove(dialogKey);
                    } catch (Exception ex) {
                        logger.warn("Failed to remove dismissed dialog from " + fieldName + " map: " + dialogKey, ex);
                    }
                    scheduledDismissals.remove(key);
                });
                dismissTimer.setRepeats(false);
                dismissTimer.start();

                logger.info("Scheduled 15s auto-dismiss for " + fieldName + " dialog: " + entry.getKey());
            }
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to schedule dismissal for " + fieldName, e);
        }
    }
}
