package org.jboss.mx.util;

import javax.management.MalformedObjectNameException;
import javax.management.ObjectName;
import java.util.Hashtable;

/**
 * Minimal compatibility shim for legacy JBoss Remoting code paths that still
 * expect the old JBossMX ObjectNameFactory utility on the runtime classpath.
 */
public final class ObjectNameFactory {

    private ObjectNameFactory() {
    }

    public static ObjectName create(String name) {
        try {
            return new ObjectName(name);
        } catch (MalformedObjectNameException e) {
            throw new Error("Malformed object name " + name, e);
        }
    }

    public static ObjectName create(String domain, String key, String value) {
        try {
            return new ObjectName(domain, key, value);
        } catch (MalformedObjectNameException e) {
            throw new Error(
                "Malformed object name " + domain + ":" + key + "=" + value,
                e
            );
        }
    }

    @SuppressWarnings("rawtypes")
    public static ObjectName create(String domain, Hashtable table) {
        try {
            return new ObjectName(domain, table);
        } catch (MalformedObjectNameException e) {
            throw new Error("Malformed object name " + domain + ":" + table, e);
        }
    }
}
