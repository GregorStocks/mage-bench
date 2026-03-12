package mage.utils;

import org.apache.log4j.PatternLayout;
import org.apache.log4j.spi.LoggingEvent;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * PatternLayout variant that renders wall-clock timestamps with microsecond precision.
 *
 * Log4j 1.x stores event timestamps as milliseconds, so this layout timestamps log
 * lines at format time instead. That gives the human-readable multi-process logs the
 * microsecond wall-clock resolution needed for cross-process ordering.
 */
public class MicrosecondPatternLayout extends PatternLayout {

    private static final String TIMESTAMP_TOKEN = "__MAGE_MICROSECOND_TIMESTAMP__";
    private static final Pattern DATE_PATTERN = Pattern.compile("%d(?:\\{([^}]*)\\})?");
    private static final String DEFAULT_TIMESTAMP_PATTERN = "yyyy-MM-dd HH:mm:ss.SSSSSS";

    private volatile PatternLayout delegate;
    private volatile String conversionPattern = DEFAULT_CONVERSION_PATTERN;
    private volatile DateTimeFormatter timestampFormatter;

    public MicrosecondPatternLayout() {
        setConversionPattern(DEFAULT_CONVERSION_PATTERN);
    }

    public MicrosecondPatternLayout(String conversionPattern) {
        setConversionPattern(conversionPattern);
    }

    @Override
    public void setConversionPattern(String conversionPattern) {
        String nextPattern = conversionPattern == null ? DEFAULT_CONVERSION_PATTERN : conversionPattern;
        super.setConversionPattern(nextPattern);
        this.conversionPattern = nextPattern;

        Matcher matcher = DATE_PATTERN.matcher(nextPattern);
        String delegatePattern;
        String timestampPattern = null;
        if (matcher.find()) {
            StringBuffer sb = new StringBuffer();
            do {
                if (timestampPattern == null) {
                    timestampPattern = matcher.group(1);
                }
                matcher.appendReplacement(sb, Matcher.quoteReplacement(TIMESTAMP_TOKEN));
            } while (matcher.find());
            matcher.appendTail(sb);
            delegatePattern = sb.toString();
        } else {
            delegatePattern = TIMESTAMP_TOKEN + " " + nextPattern;
        }

        this.timestampFormatter = DateTimeFormatter.ofPattern(normalizeTimestampPattern(timestampPattern))
                .withZone(ZoneId.systemDefault());
        this.delegate = new PatternLayout(delegatePattern);
    }

    @Override
    public String getConversionPattern() {
        return conversionPattern;
    }

    @Override
    public String format(LoggingEvent event) {
        String formatted = delegate.format(event);
        String timestamp = timestampFormatter.format(Instant.now().truncatedTo(ChronoUnit.MICROS));
        return formatted.replace(TIMESTAMP_TOKEN, timestamp);
    }

    @Override
    public boolean ignoresThrowable() {
        return delegate.ignoresThrowable();
    }

    private static String normalizeTimestampPattern(String timestampPattern) {
        if (timestampPattern == null || timestampPattern.isBlank()) {
            return DEFAULT_TIMESTAMP_PATTERN;
        }
        if (timestampPattern.contains("S")) {
            return timestampPattern.replaceAll("S+", "SSSSSS");
        }
        return timestampPattern + ".SSSSSS";
    }
}
