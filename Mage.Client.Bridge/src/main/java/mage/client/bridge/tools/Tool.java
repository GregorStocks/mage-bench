package mage.client.bridge.tools;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Marks a static method as an MCP tool.
 * The method's parameters (excluding BridgeCallbackHandler) become the tool's input schema,
 * derived automatically from Java types and @Param annotations.
 *
 * Examples are defined via a separate static examples() method on the tool class
 * using McpToolRegistry.example() and McpToolRegistry.json() helpers.
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface Tool {
    String name();
    String description();
    Field[] output();

    @Retention(RetentionPolicy.RUNTIME)
    @Target({})
    @interface Field {
        String name();
        String type();
        String description();
        String conditional() default "";
    }
}
