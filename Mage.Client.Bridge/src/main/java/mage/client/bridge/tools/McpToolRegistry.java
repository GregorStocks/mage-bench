package mage.client.bridge.tools;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import mage.client.bridge.BridgeCallbackHandler;

import java.lang.reflect.Method;
import java.lang.reflect.Parameter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Reflection-based MCP tool registry.
 * Scans tool classes for @Tool-annotated methods and auto-generates
 * input schemas from Java parameter types + @Param annotations.
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
        var ex = new HashMap<String, Object>();
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
        return new ToolEntry(toolMethod.getAnnotation(Tool.class), toolMethod, examplesMethod);
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
        Map<String, Object> result;
        try {
            result = (Map<String, Object>) entry.method().invoke(null, args);
        } catch (java.lang.reflect.InvocationTargetException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException re) throw re;
            throw new RuntimeException(cause);
        } catch (Exception e) {
            throw new RuntimeException("Failed to invoke tool " + name, e);
        }
        validateOutputKeys(name, entry, result);
        return result;
    }

    // -- Output validation --

    /** Cache of declared output field names per tool (built lazily). */
    private final Map<String, Set<String>> declaredOutputFields = new HashMap<>();

    private Set<String> getDeclaredOutputFields(ToolEntry entry) {
        return declaredOutputFields.computeIfAbsent(entry.annotation().name(), k -> {
            var fields = new HashSet<String>();
            for (Tool.Field f : entry.annotation().output()) {
                fields.add(f.name());
            }
            return fields;
        });
    }

    /**
     * Fail fast: crash if a tool returns output keys not declared in @Tool.Field.
     * This prevents tool schema and runtime code from drifting out of sync.
     */
    private void validateOutputKeys(String toolName, ToolEntry entry, Map<String, Object> result) {
        if (result == null) return;
        Set<String> declared = getDeclaredOutputFields(entry);
        for (String key : result.keySet()) {
            if (!declared.contains(key)) {
                throw new IllegalStateException(
                    "Tool '" + toolName + "' returned undeclared output key '" + key
                    + "'. Add a @Tool.Field(name = \"" + key + "\", ...) annotation to "
                    + entry.method().getDeclaringClass().getSimpleName() + ".");
            }
        }
    }

    // -- Schema generation --

    @SuppressWarnings("unchecked")
    private static Map<String, Object> buildDefinition(ToolEntry entry) {
        var def = new HashMap<String, Object>();
        def.put("name", entry.annotation().name());
        def.put("description", entry.annotation().description());
        def.put("inputSchema", buildInputSchema(entry));
        def.put("outputSchema", buildOutputSchema(entry.annotation().output()));
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
        var schema = new HashMap<String, Object>();
        schema.put("type", "object");
        var properties = new HashMap<String, Object>();
        var required = new ArrayList<String>();

        for (Parameter p : entry.method().getParameters()) {
            if (BridgeCallbackHandler.class.isAssignableFrom(p.getType())) continue;
            Param param = p.getAnnotation(Param.class);
            if (param == null) continue;

            String name = p.getName();
            var prop = new HashMap<String, Object>();
            addJsonType(prop, p.getType());
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

    private static void addJsonType(Map<String, Object> prop, Class<?> type) {
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
            var items = new HashMap<String, Object>();
            items.put("type", "string");
            prop.put("items", items);
        } else if (type == int[].class) {
            prop.put("type", "array");
            var items = new HashMap<String, Object>();
            items.put("type", "integer");
            prop.put("items", items);
        } else {
            throw new RuntimeException("Unsupported parameter type: " + type.getName());
        }
    }

    private static Map<String, Object> buildOutputSchema(Tool.Field[] fields) {
        var schema = new HashMap<String, Object>();
        schema.put("type", "object");
        var properties = new HashMap<String, Object>();
        for (Tool.Field f : fields) {
            var prop = new HashMap<String, Object>();
            String type = f.type();
            if (type.startsWith("array[") && type.endsWith("]")) {
                prop.put("type", "array");
                var items = new HashMap<String, Object>();
                items.put("type", type.substring(6, type.length() - 1));
                prop.put("items", items);
            } else {
                prop.put("type", type);
            }
            prop.put("description", f.description());
            if (!f.conditional().isEmpty()) {
                prop.put("conditional", f.conditional());
            }
            properties.put(f.name(), prop);
        }
        schema.put("properties", properties);
        return schema;
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

    private record ToolEntry(Tool annotation, Method method, Method examplesMethod) {}
}
