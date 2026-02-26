package mage.client.observer.recording;

import org.apache.log4j.Logger;

import java.awt.image.BufferedImage;
import java.awt.image.DataBufferByte;
import java.awt.image.DataBufferInt;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Encodes frames to video using FFmpeg.
 * Pipes raw RGB frames to FFmpeg via stdin for H.264 encoding.
 */
public class FFmpegEncoder implements FrameConsumer {

    private static final Logger logger = Logger.getLogger(FFmpegEncoder.class);

    private final Path outputPath;
    private Process ffmpeg;
    private OutputStream stdin;
    private int width;
    private int height;
    private byte[] rgbBuffer;
    private byte[] prevRgbBuffer;
    private volatile boolean pipeBroken;

    public FFmpegEncoder(Path outputPath) {
        this.outputPath = outputPath;
    }

    @Override
    public void start(int width, int height, int fps) throws Exception {
        this.width = width;
        this.height = height;
        this.rgbBuffer = new byte[width * height * 3];
        this.prevRgbBuffer = new byte[width * height * 3];

        List<String> command = new ArrayList<>();
        command.add("ffmpeg");
        command.add("-y"); // Overwrite output
        command.add("-use_wallclock_as_timestamps"); command.add("1");
        command.add("-f"); command.add("rawvideo");
        command.add("-pix_fmt"); command.add("rgb24");
        command.add("-s"); command.add(width + "x" + height);
        command.add("-r"); command.add(String.valueOf(fps));
        command.add("-i"); command.add("-"); // Read from stdin
        // Pad to even dimensions (H.264 requires width/height divisible by 2)
        command.add("-vf"); command.add("pad=ceil(iw/2)*2:ceil(ih/2)*2");
        command.add("-c:v"); command.add("libx264");
        command.add("-preset"); command.add("ultrafast"); // Low CPU, fast encoding
        command.add("-crf"); command.add("23"); // Good quality/size balance
        command.add("-pix_fmt"); command.add("yuv420p"); // Compatibility
        command.add("-fps_mode"); command.add("vfr");
        command.add(outputPath.toString());

        logger.info("Starting FFmpeg: " + String.join(" ", command));

        ProcessBuilder pb = new ProcessBuilder(command);
        pb.redirectErrorStream(true);
        // Redirect FFmpeg output to a log file next to the video
        Path logPath = outputPath.resolveSibling(
            outputPath.getFileName().toString().replace(".mov", "_ffmpeg.log")
        );
        pb.redirectOutput(logPath.toFile());

        ffmpeg = pb.start();
        stdin = ffmpeg.getOutputStream();

        logger.info("Recording to: " + outputPath);
    }

    @Override
    public void consumeFrame(BufferedImage frame, long frameNumber) {
        if (stdin == null || pipeBroken) {
            return;
        }

        try {
            convertToRgb24(frame, rgbBuffer);
            if (Arrays.equals(rgbBuffer, prevRgbBuffer)) {
                return; // Skip pixel-identical frame; VFR extends previous frame's duration
            }
            stdin.write(rgbBuffer);
            System.arraycopy(rgbBuffer, 0, prevRgbBuffer, 0, rgbBuffer.length);
        } catch (IOException e) {
            pipeBroken = true;
            logger.error("Failed to write frame " + frameNumber + ", stopping further writes", e);
        }
    }

    @Override
    public void close() {
        if (stdin != null) {
            try {
                stdin.close();
            } catch (IOException e) {
                logger.warn("Error closing FFmpeg stdin", e);
            }
        }

        if (ffmpeg != null) {
            try {
                int exitCode = ffmpeg.waitFor();
                if (exitCode == 0) {
                    logger.info("Recording saved: " + outputPath);
                } else {
                    logger.warn("FFmpeg exited with code: " + exitCode);
                }
            } catch (InterruptedException e) {
                logger.warn("Interrupted waiting for FFmpeg", e);
                ffmpeg.destroyForcibly();
            }
        }
    }

    /**
     * Convert a BufferedImage to RGB24 byte array.
     * Handles both INT_RGB and 3BYTE_BGR formats efficiently.
     */
    private void convertToRgb24(BufferedImage image, byte[] output) {
        int w = image.getWidth();
        int h = image.getHeight();

        if (image.getType() == BufferedImage.TYPE_INT_RGB ||
            image.getType() == BufferedImage.TYPE_INT_ARGB) {
            // Fast path for INT formats
            int[] pixels = ((DataBufferInt) image.getRaster().getDataBuffer()).getData();
            int idx = 0;
            for (int pixel : pixels) {
                output[idx++] = (byte) ((pixel >> 16) & 0xFF); // R
                output[idx++] = (byte) ((pixel >> 8) & 0xFF);  // G
                output[idx++] = (byte) (pixel & 0xFF);         // B
            }
        } else if (image.getType() == BufferedImage.TYPE_3BYTE_BGR) {
            // Fast path for BGR format
            byte[] pixels = ((DataBufferByte) image.getRaster().getDataBuffer()).getData();
            int idx = 0;
            for (int i = 0; i < pixels.length; i += 3) {
                output[idx++] = pixels[i + 2]; // R (was B)
                output[idx++] = pixels[i + 1]; // G
                output[idx++] = pixels[i];     // B (was R)
            }
        } else {
            // Slow fallback for other formats
            int idx = 0;
            for (int y = 0; y < h; y++) {
                for (int x = 0; x < w; x++) {
                    int rgb = image.getRGB(x, y);
                    output[idx++] = (byte) ((rgb >> 16) & 0xFF);
                    output[idx++] = (byte) ((rgb >> 8) & 0xFF);
                    output[idx++] = (byte) (rgb & 0xFF);
                }
            }
        }
    }
}
