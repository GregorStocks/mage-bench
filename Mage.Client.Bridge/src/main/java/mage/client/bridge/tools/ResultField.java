package mage.client.bridge.tools;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Marks a public field on a tool result class as part of the tool's output schema.
 * McpToolRegistry derives the output schema from these annotations via reflection,
 * and converts result objects to Map&lt;String, Object&gt; by reading annotated fields.
 *
 * Field names use underscores (e.g. {@code action_pending}) to match JSON output keys.
 * The JSON type is derived from the Java field type automatically.
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.FIELD)
public @interface ResultField {
    String description();
    String conditional() default "";
}
