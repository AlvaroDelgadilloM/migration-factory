package demo.pedidos;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import java.util.HashMap;
import java.util.Map;

import org.junit.Test;

public class ValidadorPedidoTest {

    private static Map<String, Object> pedido(Object cliente, Object producto, Object cantidad) {
        Map<String, Object> m = new HashMap<>();
        m.put("clienteId", cliente);
        m.put("productoId", producto);
        m.put("cantidad", cantidad);
        return m;
    }

    @Test
    public void unPedidoValidoQuedaComoEnteros() {
        Map<String, Object> v = ValidadorPedido.validar(pedido(1, "2", 3));
        assertEquals(1, v.get("clienteId"));
        assertEquals(2, v.get("productoId"));
        assertEquals(3, v.get("cantidad"));
    }

    @Test
    public void cantidadFueraDeRango() {
        for (Object cantidad : new Object[] {0, 101, -5}) {
            try {
                ValidadorPedido.validar(pedido(1, 2, cantidad));
                fail("debió rechazar " + cantidad);
            } catch (Errores.Validacion e) {
                assertEquals("La cantidad debe estar entre 1 y 100", e.getMessage());
            }
        }
    }

    @Test
    public void faltaUnCampoONoEsNumero() {
        try {
            ValidadorPedido.validar(pedido(1, null, 2));
            fail();
        } catch (Errores.Validacion e) {
            assertEquals("Falta el campo productoId", e.getMessage());
        }
        try {
            ValidadorPedido.validar(pedido("uno", 2, 2));
            fail();
        } catch (Errores.Validacion e) {
            assertEquals("El campo clienteId debe ser un número entero", e.getMessage());
        }
    }
}
