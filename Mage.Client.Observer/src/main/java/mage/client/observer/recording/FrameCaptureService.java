package mage.client.observer.recording;

import org.apache.log4j.Logger;

import javax.swing.*;
import java.awt.*;
import java.awt.image.BufferedImage;

/**
 * Captures a Swing component to video frames at a specified frame rate.
 *
 * Uses a Swing Timer to capture frames on the EDT, ensuring thread safety
 * with Swing components. Frames are passed to a FrameConsumer for encoding.
 */
public class FrameCaptureService {

    private static final Logger logger = Logger.getLogger(FrameCaptureService.class);

    private final JComponent target;
    private final int fps;
    private final FrameConsumer consumer;

    private Timer captureTimer;
    private long frameNumber = 0;
    private long startTimeNanos;
    private BufferedImage frameBuffer;
    private boolean running = false;

    /**
     * Create a new frame capture service.
     *
     * @param target The Swing component to capture
     * @param fps Target frames per second (typically 30)
     * @param consumer The consumer that will receive captured frames
     */
    public FrameCaptureService(JComponent target, int fps, FrameConsumer consumer) {
        this.target = target;
        this.fps = fps;
        this.consumer = consumer;
    }

    /**
     * Start capturing frames.
     * Must be called on the EDT.
     */
    public void start() {
        if (running) {
            logger.warn("FrameCaptureService already running");
            return;
        }

        int width = target.getWidth();
        int height = target.getHeight();

        if (width <= 0 || height <= 0) {
            logger.error("Cannot start capture: target has no size (" + width + "x" + height + ")");
            return;
        }

        logger.info("Starting frame capture: " + width + "x" + height + " @ " + fps + "fps");

        // Initialize the frame buffer (reused for efficiency)
        frameBuffer = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);

        // Initialize the consumer
        try {
            consumer.start(width, height, fps);
        } catch (Exception e) {
            logger.error("Failed to start frame consumer", e);
            return;
        }

        running = true;
        frameNumber = 0;
        startTimeNanos = System.nanoTime();

        // Calculate timer delay in milliseconds
        int delayMs = 1000 / fps;

        // Create timer that fires on EDT
        captureTimer = new Timer(delayMs, e -> captureFrame());
        captureTimer.start();
    }

    /**
     * Stop capturing frames.
     * Can be called from any thread.
     */
    public void stop() {
        if (!running) {
            return;
        }

        running = false;

        if (captureTimer != null) {
            captureTimer.stop();
            captureTimer = null;
        }

        // Close the consumer (finalizes video file)
        consumer.close();

        long wallClockMs = (System.nanoTime() - startTimeNanos) / 1_000_000;
        logger.info("Frame capture stopped: " + frameNumber + " frames in "
                + wallClockMs / 1000 + "." + String.format("%03d", wallClockMs % 1000) + "s");
    }

    /**
     * Capture a single frame.
     * Called by the timer on the EDT.
     *
     * Timing accuracy is handled by FFmpeg's -use_wallclock_as_timestamps
     * option, which stamps each frame when it arrives. Under load, video
     * will be choppier (lower fps) but correctly timed.
     */
    private void captureFrame() {
        if (!running || target == null) {
            return;
        }

        // Check if target size changed (window resize)
        int currentWidth = target.getWidth();
        int currentHeight = target.getHeight();

        if (currentWidth != frameBuffer.getWidth() || currentHeight != frameBuffer.getHeight()) {
            // For now, log a warning. FFmpeg doesn't handle resolution changes mid-stream.
            // Future: could restart encoder or scale frames.
            logger.warn("Target size changed from " + frameBuffer.getWidth() + "x" + frameBuffer.getHeight() +
                       " to " + currentWidth + "x" + currentHeight + " - frames may be cropped/padded");
        }

        // Paint the component into our buffer
        Graphics2D g = frameBuffer.createGraphics();
        try {
            // Clear to black in case component is smaller
            g.setColor(Color.BLACK);
            g.fillRect(0, 0, frameBuffer.getWidth(), frameBuffer.getHeight());

            // Paint the component
            target.paint(g);

        } finally {
            g.dispose();
        }

        consumer.consumeFrame(frameBuffer, frameNumber++);
    }

    /**
     * Check if capture is currently running.
     */
    public boolean isRunning() {
        return running;
    }

    /**
     * Get the number of frames captured so far.
     */
    public long getFrameCount() {
        return frameNumber;
    }
}
