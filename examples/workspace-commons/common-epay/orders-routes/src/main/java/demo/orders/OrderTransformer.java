package demo.orders;

/** Lógica de negocio pura: debe comportarse igual antes y después de migrar. */
public class OrderTransformer {
    public String normalize(String csv) {
        if (csv == null || csv.trim().isEmpty()) {
            throw new IllegalArgumentException("pedido vacío");
        }
        String[] parts = csv.split(";");
        if (parts.length != 2) {
            throw new IllegalArgumentException("formato esperado id;importe");
        }
        return "ORDER id=" + parts[0].trim() + " amount=" + new java.math.BigDecimal(parts[1].trim()).setScale(2);
    }
}
