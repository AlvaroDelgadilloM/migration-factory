package demo.catalog;

import java.util.Map;
import java.util.TreeMap;

/** In-memory catalog: the JSON is written here so the answers do not depend on a JSON library version. */
public final class Catalog {
    private static final Map<Integer, String[]> ITEMS = new TreeMap<>();
    static {
        String[] cats = {"hogar", "viaje", "bienestar"};
        for (int i = 1; i <= 12; i++) {
            ITEMS.put(i, new String[]{"Artículo " + i, cats[i % 3], String.format(java.util.Locale.ROOT, "%.2f", 100 + i * 17.5)});
        }
    }

    private Catalog() {
    }

    private static String json(int id, String[] it) {
        return "{\"id\":" + id + ",\"name\":\"" + it[0] + "\",\"category\":\"" + it[1] + "\",\"price\":" + it[2] + "}";
    }

    public static String all(String category) {
        StringBuilder sb = new StringBuilder("[");
        for (Map.Entry<Integer, String[]> e : ITEMS.entrySet()) {
            if (category == null || category.equals(e.getValue()[1])) {
                sb.append(sb.length() > 1 ? "," : "").append(json(e.getKey(), e.getValue()));
            }
        }
        return sb.append(']').toString();
    }

    public static String one(String id) {
        try {
            String[] it = ITEMS.get(Integer.parseInt(id));
            return it == null ? null : json(Integer.parseInt(id), it);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    public static String stats() {
        Map<String, Integer> byCat = new TreeMap<>();
        for (String[] it : ITEMS.values()) {
            byCat.merge(it[1], 1, Integer::sum);
        }
        StringBuilder sb = new StringBuilder("{\"total\":" + ITEMS.size() + ",\"byCategory\":{");
        byCat.forEach((k, v) -> sb.append(sb.charAt(sb.length() - 1) == '{' ? "" : ",").append('"').append(k).append("\":").append(v));
        return sb.append("}}").toString();
    }

    /** Order validation: quantity 1..50 and an existing item. */
    public static String validate(String body) {
        java.util.regex.Matcher q = java.util.regex.Pattern.compile("\"quantity\"\\s*:\\s*(-?\\d+)").matcher(body == null ? "" : body);
        java.util.regex.Matcher i = java.util.regex.Pattern.compile("\"itemId\"\\s*:\\s*(\\d+)").matcher(body == null ? "" : body);
        if (!i.find() || !ITEMS.containsKey(Integer.parseInt(i.group(1)))) {
            return "ERROR:Artículo inexistente";
        }
        if (!q.find() || Integer.parseInt(q.group(1)) < 1 || Integer.parseInt(q.group(1)) > 50) {
            return "ERROR:quantity fuera de rango (1-50)";
        }
        return "{\"valid\":true,\"itemId\":" + i.group(1) + ",\"quantity\":" + q.group(1) + "}";
    }
}
