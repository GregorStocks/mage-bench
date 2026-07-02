package mage.interfaces;

import mage.utils.CompressUtil;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

public class WatchResultTest {

    @Test
    void okIsSuccess() {
        assertThat(WatchResult.ok().isSuccess()).isTrue();
    }

    @Test
    void failCarriesReason() {
        WatchResult result = WatchResult.fail("table is not DUELING");
        assertThat(result.isSuccess()).isFalse();
        assertThat(result.getFailReason()).isEqualTo("table is not DUELING");
    }

    @Test
    void getFailReasonThrowsOnSuccess() {
        assertThatThrownBy(() -> WatchResult.ok().getFailReason())
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void failRejectsNullOrBlankReason() {
        assertThatThrownBy(() -> WatchResult.fail(null))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> WatchResult.fail("   "))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void survivesSerializationRoundTrip() {
        // WatchResult crosses the JBoss Remoting wire as an RPC return value
        assertThat(roundTrip(WatchResult.ok()).isSuccess()).isTrue();

        WatchResult fail = roundTrip(WatchResult.fail("user is banned"));
        assertThat(fail.isSuccess()).isFalse();
        assertThat(fail.getFailReason()).isEqualTo("user is banned");
    }

    private WatchResult roundTrip(WatchResult original) {
        return (WatchResult) CompressUtil.decompress(CompressUtil.compress(original));
    }
}
