package mage.client.bridge.tools;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import mage.client.bridge.BridgeCallbackHandler;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Parameter;
import java.lang.reflect.ParameterizedType;
import java.lang.reflect.Type;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Reflection-based MCP tool registry.
 * Scans tool classes for @Tool-annotated methods and auto-generates
 * input schemas from Java parameter types + @Param annotations,
 * output schemas from @ResultField-annotated fields on the return type.
 */
public class McpToolRegistry {

    private static final Gson PRETTY_GSON = new GsonBuilder()
            .setPrettyPrinting().disableHtmlEscaping().create();

    private final List<ToolEntry> entries = new ArrayList<>();
    private final Map<String, ToolEntry> byName = new LinkedHashMap<>();

    public McpToolRegistry(Class<?>... toolClasses) {
        for (Class<?> cls : toolClasses) {
            ToolEntry entry = scan(cls);
            entries.add(entry);
            byName.put(entry.annotation().name(), entry);
        }
    }

    // -- Helpers for building examples in tool classes --

    /** Build a JSON object (LinkedHashMap) from alternating key-value pairs. */
    public static Map<String, Object> json(Object... kvs) {
        var map = new LinkedHashMap<String, Object>();
        for (int i = 0; i < kvs.length; i += 2) {
            map.put((String) kvs[i], kvs[i + 1]);
        }
        return map;
    }

    /** Build an example entry from a label and a value map (serialized to pretty JSON). */
    public static Map<String, Object> example(String label, Map<String, Object> value) {
        var ex = new LinkedHashMap<String, Object>();
        ex.put("label", label);
        ex.put("value", PRETTY_GSON.toJson(value));
        return ex;
    }

    // -- Registry core --

    private static ToolEntry scan(Class<?> cls) {
        Method toolMethod = null;
        Method examplesMethod = null;
        for (Method m : cls.getDeclaredMethods()) {
            if (m.getAnnotation(Tool.class) != null) {
                toolMethod = m;
            } else if ("examples".equals(m.getName())) {
                examplesMethod = m;
            }
        }
        if (toolMethod == null) {
            throw new RuntimeException("No @Tool method found in " + cls.getName());
        }
        Class<?> resultClass = toolMethod.getReturnType();
        return new ToolEntry(toolMethod.getAnnotation(Tool.class), toolMethod, examplesMethod, resultClass);
    }

    /** Build the full tool definition list (for tools/list and JSON export). */
    public List<Map<String, Object>> getDefinitions() {
        var defs = new ArrayList<Map<String, Object>>();
        for (ToolEntry entry : entries) {
            defs.add(buildDefinition(entry));
        }
        return defs;
    }

    /** Execute a tool by name, extracting args from the JsonObject. */
    @SuppressWarnings("unchecked")
    public Map<String, Object> call(String name, JsonObject arguments, BridgeCallbackHandler handler) {
        // Some models (e.g. Kimi K2.5) emit tool names with leading whitespace
        name = name.strip();
        ToolEntry entry = byName.get(name);
        if (entry == null) {
            throw new RuntimeException("Unknown tool: " + name);
        }
        Parameter[] params = entry.method().getParameters();
        var args = new Object[params.length];
        for (int i = 0; i < params.length; i++) {
            Class<?> type = params[i].getType();
            if (BridgeCallbackHandler.class.isAssignableFrom(type)) {
                args[i] = handler;
            } else {
                String paramName = params[i].getName();
                args[i] = extractArg(arguments, paramName, type);
            }
        }
        Object rawResult;
        try {
            rawResult = entry.method().invoke(null, args);
        } catch (java.lang.reflect.InvocationTargetException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException re) throw re;
            throw new RuntimeException(cause);
        } catch (Exception e) {
            throw new RuntimeException("Failed to invoke tool " + name, e);
        }
        return resultToMap(rawResult);
    }

    // -- Result conversion --

    /**
     * Convert a typed result object to a Map by reading @ResultField-annotated public fields.
     * Null fields are omitted from the output.
     */
    public static Map<String, Object> resultToMap(Object result) {
        if (result == null) return new LinkedHashMap<>();
        var map = new LinkedHashMap<String, Object>();
        for (Field f : getResultFields(result.getClass())) {
            try {
                Object value = f.get(result);
                if (value != null) {
                    map.put(f.getName(), value);
                }
            } catch (IllegalAccessException e) {
                throw new RuntimeException("Cannot read field " + f.getName(), e);
            }
        }
        return map;
    }

    /**
     * Collect all @ResultField-annotated public fields from a class and its superclasses,
     * in declaration order (superclass fields first).
     */
    private static List<Field> getResultFields(Class<?> cls) {
        var fields = new ArrayList<Field>();
        // Walk superclass chain (superclass fields come first)
        var hierarchy = new ArrayList<Class<?>>();
        for (Class<?> c = cls; c != null && c != Object.class; c = c.getSuperclass()) {
            hierarchy.add(c);
        }
        // Reverse to process superclass first
        for (int i = hierarchy.size() - 1; i >= 0; i--) {
            for (Field f : hierarchy.get(i).getDeclaredFields()) {
                if (f.isAnnotationPresent(ResultField.class)) {
                    fields.add(f);
                }
            }
        }
        return fields;
    }

    // -- Schema generation --

    @SuppressWarnings("unchecked")
    private static Map<String, Object> buildDefinition(ToolEntry entry) {
        var def = new LinkedHashMap<String, Object>();
        def.put("name", entry.annotation().name());
        def.put("description", entry.annotation().description());
        def.put("inputSchema", buildInputSchema(entry));
        def.put("outputSchema", buildOutputSchema(entry.resultClass()));
        if (entry.examplesMethod() != null) {
            try {
                def.put("examples", (List<Map<String, Object>>) entry.examplesMethod().invoke(null));
            } catch (Exception e) {
                throw new RuntimeException("Failed to invoke examples() on " + entry.method().getDeclaringClass().getName(), e);
            }
        }
        return def;
    }

    private static Map<String, Object> buildInputSchema(ToolEntry entry) {
        var schema = new LinkedHashMap<String, Object>();
        schema.put("type", "object");
        var properties = new LinkedHashMap<String, Object>();
        var required = new ArrayList<String>();

        for (Parameter p : entry.method().getParameters()) {
            if (BridgeCallbackHandler.class.isAssignableFrom(p.getType())) continue;
            Param param = p.getAnnotation(Param.class);
            if (param == null) continue;

            String name = p.getName();
            var prop = new LinkedHashMap<String, Object>();
            addJsonTypeForParam(prop, p.getType());
            prop.put("description", param.description());
            if (param.allowed_values().length > 0) {
                prop.put("enum", List.of(param.allowed_values()));
            }
            properties.put(name, prop);

            if (param.required()) {
                required.add(name);
            }
        }

        schema.put("properties", properties);
        if (!required.isEmpty()) {
            schema.put("required", required);
        }
        schema.put("additionalProperties", false);
        return schema;
    }

    private static void addJsonTypeForParam(Map<String, Object> prop, Class<?> type) {
        if (type == String.class) {
            prop.put("type", "string");
        } else if (type == Integer.class || type == int.class) {
            prop.put("type", "integer");
        } else if (type == Long.class || type == long.class) {
            prop.put("type", "integer");
        } else if (type == Boolean.class || type == boolean.class) {
            prop.put("type", "boolean");
        } else if (type == String[].class) {
            prop.put("type", "array");
            var items = new LinkedHashMap<String, Object>();
            items.put("type", "string");
            prop.put("items", items);
        } else if (type == int[].class) {
            prop.put("type", "array");
            var items = new LinkedHashMap<String, Object>();
            items.put("type", "integer");
            prop.put("items", items);
        } else {
            throw new RuntimeException("Unsupported parameter type: " + type.getName());
        }
    }

    /**
     * Build the output schema from a result class's @ResultField-annotated fields.
     */
    private static Map<String, Object> buildOutputSchema(Class<?> resultClass) {
        var schema = new LinkedHashMap<String, Object>();
        schema.put("type", "object");
        var properties = new LinkedHashMap<String, Object>();
        for (Field f : getResultFields(resultClass)) {
            ResultField rf = f.getAnnotation(ResultField.class);
            var prop = new LinkedHashMap<String, Object>();
            addJsonTypeForField(prop, f);
            prop.put("description", rf.description());
            if (!rf.conditional().isEmpty()) {
                prop.put("conditional", rf.conditional());
            }
            properties.put(f.getName(), prop);
        }
        schema.put("properties", properties);
        return schema;
    }

    /**
     * Derive JSON schema type from a result field's Java type, including generic type parameters.
     */
    private static void addJsonTypeForField(Map<String, Object> prop, Field f) {
        Class<?> type = f.getType();
        if (type == String.class) {
            prop.put("type", "string");
        } else if (type == Integer.class || type == int.class) {
            prop.put("type", "integer");
        } else if (type == Long.class || type == long.class) {
            prop.put("type", "integer");
        } else if (type == Boolean.class || type == boolean.class) {
            prop.put("type", "boolean");
        } else if (List.class.isAssignableFrom(type)) {
            prop.put("type", "array");
            var items = new LinkedHashMap<String, Object>();
            items.put("type", getListItemType(f));
            prop.put("items", items);
        } else if (Map.class.isAssignableFrom(type)) {
            prop.put("type", "object");
        } else {
            throw new RuntimeException("Unsupported result field type: " + type.getName()
                + " on field " + f.getName());
        }
    }

    /**
     * Determine the JSON type of a List field's element type via generic type parameters.
     * Returns "string" for List&lt;String&gt;, "object" for everything else.
     */
    private static String getListItemType(Field f) {
        Type genericType = f.getGenericType();
        if (genericType instanceof ParameterizedType pt) {
            Type[] typeArgs = pt.getActualTypeArguments();
            if (typeArgs.length == 1 && typeArgs[0] == String.class) {
                return "string";
            }
        }
        return "object";
    }

    // -- Arg extraction --

    private static Object extractArg(JsonObject obj, String key, Class<?> type) {
        if (!obj.has(key) || obj.get(key).isJsonNull()) return null;

        try {
            if (type == String.class) {
                return obj.get(key).getAsString();
            } else if (type == Integer.class) {
                return obj.get(key).getAsInt();
            } else if (type == Long.class) {
                return obj.get(key).getAsLong();
            } else if (type == Boolean.class) {
                return obj.get(key).getAsBoolean();
            } else if (type == String[].class) {
                JsonArray arr = obj.getAsJsonArray(key);
                var result = new String[arr.size()];
                for (int i = 0; i < arr.size(); i++) {
                    JsonElement elem = arr.get(i);
                    result[i] = elem.isJsonNull() ? null : elem.getAsString();
                }
                return result;
            } else if (type == int[].class) {
                JsonArray arr = obj.getAsJsonArray(key);
                var result = new int[arr.size()];
                for (int i = 0; i < arr.size(); i++) {
                    result[i] = arr.get(i).isJsonNull() ? 0 : arr.get(i).getAsInt();
                }
                return result;
            }
        } catch (UnsupportedOperationException | ClassCastException | IllegalStateException e) {
            String actualType = obj.get(key).getClass().getSimpleName();
            throw new IllegalArgumentException(
                "Parameter '" + key + "': expected " + jsonTypeName(type)
                + ", got " + actualType + " (" + truncate(obj.get(key).toString(), 80)
                + "). Check the tool schema for correct parameter types.");
        }
        throw new RuntimeException("Unsupported parameter type: " + type.getName());
    }

    private static String jsonTypeName(Class<?> type) {
        if (type == String.class) return "string";
        if (type == Integer.class) return "integer";
        if (type == Long.class) return "integer";
        if (type == Boolean.class) return "boolean";
        if (type == String[].class) return "array of strings";
        if (type == int[].class) return "array of integers";
        return type.getSimpleName();
    }

    private static String truncate(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max) + "...";
    }

    private record ToolEntry(Tool annotation, Method method, Method examplesMethod, Class<?> resultClass) {}
}
