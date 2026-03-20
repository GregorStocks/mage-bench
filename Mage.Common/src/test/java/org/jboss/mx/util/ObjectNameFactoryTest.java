package org.jboss.mx.util;

import org.jboss.remoting.transporter.InternalTransporterServices;
import org.junit.jupiter.api.Test;

import javax.management.ObjectName;
import java.util.Hashtable;

import static org.assertj.core.api.Assertions.assertThat;

class ObjectNameFactoryTest {

    @Test
    void createsObjectNamesWithLegacyHelpers() {
        ObjectName fromString = ObjectNameFactory.create("remoting:type=Detector");
        ObjectName fromParts = ObjectNameFactory.create("remoting", "type", "NetworkRegistry");
        Hashtable<String, String> properties = new Hashtable<>();
        properties.put("type", "Detector");
        ObjectName fromTable = ObjectNameFactory.create("remoting", properties);

        assertThat(fromString.getDomain()).isEqualTo("remoting");
        assertThat(fromString.getKeyProperty("type")).isEqualTo("Detector");
        assertThat(fromParts.getKeyProperty("type")).isEqualTo("NetworkRegistry");
        assertThat(fromTable.getKeyProperty("type")).isEqualTo("Detector");
    }

    @Test
    void allowsLegacyRemotingTransporterToInitialize() {
        assertThat(InternalTransporterServices.getInstance()).isNotNull();
        assertThat(InternalTransporterServices.DEFAULT_DETECTOR_OBJECTNAME).isNotNull();
        assertThat(InternalTransporterServices.DEFAULT_NETWORKREGISTRY_OBJECTNAME).isNotNull();
    }
}
