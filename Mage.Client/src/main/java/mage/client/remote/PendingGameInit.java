package mage.client.remote;

import mage.view.GameView;

record PendingGameInit(int messageId, GameView gameView) {
}
