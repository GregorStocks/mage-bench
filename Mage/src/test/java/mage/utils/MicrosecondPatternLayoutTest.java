package mage.utils;

import org.apache.log4j.Level;
import org.apache.log4j.Logger;
import org.apache.log4j.spi.LoggingEvent;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class MicrosecondPatternLayoutTest {

    @Test
    void formatsBridgeStylePatternWithMicroseconds() {
        MicrosecondPatternLayout layout = new MicrosecondPatternLayout("[%d{HH:mm:ss}] %-5p %m%n");

        String formatted = layout.format(loggingEvent("bridge message"));

        assertThat(formatted).matches("\\[\\d{2}:\\d{2}:\\d{2}\\.\\d{6}] INFO\\s+bridge message\\R");
    }

    @Test
    void expandsMillisecondPatternToMicroseconds() {
        MicrosecondPatternLayout layout = new MicrosecondPatternLayout(
            "%-5p %d{yyyy-MM-dd HH:mm:ss,SSS} %m%n");

        String formatted = layout.format(loggingEvent("server message"));

        assertThat(formatted)
            .matches("INFO\\s+\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2},\\d{6} server message\\R");
    }

    private static LoggingEvent loggingEvent(String message) {
        return new LoggingEvent(
            MicrosecondPatternLayoutTest.class.getName(),
            Logger.getLogger("microsecond-pattern-layout-test"),
            System.currentTimeMillis(),
            Level.INFO,
            message,
            null);
    }
}
