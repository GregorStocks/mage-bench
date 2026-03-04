package mage.client.bridge.tools;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Marks a static method as an MCP tool.
 * The method's parameters (excluding BridgeCallbackHandler) become the tool's input schema,
 * derived automatically from Java types and @Param annotations.
 * The method's return type must be a class with @ResultField-annotated public fields,
 * from which the output schema is derived automatically.
 *
 * Examples are defined via a separate static examples() method on the tool class
 * using McpToolRegistry.example() and McpToolRegistry.json() helpers.
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface Tool {
    String name();
    String description();
}
