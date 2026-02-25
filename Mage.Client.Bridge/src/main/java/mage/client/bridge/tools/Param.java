package mage.client.bridge.tools;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Annotates a tool method parameter with its description and whether it's required.
 * The parameter name and type are derived from the Java signature via reflection.
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.PARAMETER)
public @interface Param {
    String description();
    boolean required() default false;
    /** If non-empty, emitted as a JSON Schema "enum" array constraining valid values. */
    String[] allowed_values() default {};
}
