package mage.client.remote;

import mage.view.GameView;

record PendingGameView(int messageId, GameView gameView) {
}
