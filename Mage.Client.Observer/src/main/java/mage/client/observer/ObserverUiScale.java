package mage.client.observer;

import javax.swing.*;
import java.awt.*;

final class ObserverUiScale {

    private ObserverUiScale() {
    }

    static double computeScaleFactor(Component component) {
        return scaleFactorForHeight(resolveWindowHeight(component));
    }

    static int computeAvatarSize(Component component) {
        return avatarSizeForHeight(resolveWindowHeight(component));
    }

    static double scaleFactorForHeight(int windowHeight) {
        double scale = windowHeight / 1080.0;
        return Math.max(1.0, Math.min(scale, 2.5));
    }

    static int avatarSizeForHeight(int windowHeight) {
        int avatarSize = windowHeight / 11;
        return Math.max(80, Math.min(avatarSize, 300));
    }

    private static int resolveWindowHeight(Component component) {
        Window window = component == null ? null : SwingUtilities.getWindowAncestor(component);
        if (window != null && window.getHeight() > 0) {
            return window.getHeight();
        }
        return Toolkit.getDefaultToolkit().getScreenSize().height;
    }
}
