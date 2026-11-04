package demo.pedidos;

import java.util.LinkedHashMap;
import java.util.Map;

import org.apache.camel.Exchange;

/** Valida el cuerpo de un pedido nuevo y deja clienteId, productoId y cantidad como headers enteros. */
public class ValidadorPedido {

    public static final int CANTIDAD_MAXIMA = 100;

    public void validar(Exchange exchange) {
        @SuppressWarnings("unchecked")
        Map<String, Object> body = exchange.getIn().getBody(Map.class);
        exchange.getIn().getHeaders().putAll(validar(body));
    }

    /** Sin Camel, para las pruebas unitarias. */
    public static Map<String, Object> validar(Map<String, Object> body) {
        if (body == null) {
            throw new Errores.Validacion("El cuerpo del pedido es obligatorio");
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("clienteId", entero(body, "clienteId"));
        out.put("productoId", entero(body, "productoId"));
        int cantidad = entero(body, "cantidad");
        if (cantidad < 1 || cantidad > CANTIDAD_MAXIMA) {
            throw new Errores.Validacion("La cantidad debe estar entre 1 y " + CANTIDAD_MAXIMA);
        }
        out.put("cantidad", cantidad);
        return out;
    }

    private static int entero(Map<String, Object> body, String campo) {
        Object v = body.get(campo);
        if (v == null) {
            throw new Errores.Validacion("Falta el campo " + campo);
        }
        try {
            return Integer.parseInt(v.toString().trim());
        } catch (NumberFormatException e) {
            throw new Errores.Validacion("El campo " + campo + " debe ser un número entero");
        }
    }
}
