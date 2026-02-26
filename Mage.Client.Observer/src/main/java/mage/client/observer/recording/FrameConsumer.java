package mage.client.observer.recording;

import java.awt.image.BufferedImage;

/**
 * Interface for consuming captured frames from FrameCaptureService.
 * Implementations handle encoding/writing frames to various destinations.
 */
public interface FrameConsumer {

    /**
     * Called when recording starts to allow consumer to initialize.
     * @param width Frame width in pixels
     * @param height Frame height in pixels
     * @param fps Target frames per second
     */
    void start(int width, int height, int fps) throws Exception;

    /**
     * Consume a captured frame.
     * @param frame The captured frame as a BufferedImage
     * @param frameNumber The frame sequence number (0-indexed)
     */
    void consumeFrame(BufferedImage frame, long frameNumber);

    /**
     * Called when recording stops. Should finalize output and release resources.
     */
    void close();
}
