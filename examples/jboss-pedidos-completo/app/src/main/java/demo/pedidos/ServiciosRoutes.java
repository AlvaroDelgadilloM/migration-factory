package demo.pedidos;

import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.component.cxf.common.message.CxfConstants;
import org.apache.camel.component.sql.SqlConstants;

/**
 * Rutas internas sin manejo de errores propio: un error sube a quien las llamó (PedidosRoutes lo traduce a HTTP) y, en la ruta
 * transaccional, deshace todo lo que hizo en la base. Aquí también el SOAP publicado: un error se vuelve SOAP Fault.
 */
public class ServiciosRoutes extends RouteBuilder {

    private static final String DS = PedidosRoutes.DS;

    @Override
    public void configure() {
        // SOAP consumido: el buró de crédito (timeout de 3 s en el conduit de applicationContext.xml)
        from("direct:evaluarCredito").routeId("evaluar-credito")
            .bean("soap", "peticionCredito")
            .setHeader(CxfConstants.OPERATION_NAME, constant("evaluar"))
            .setHeader(CxfConstants.OPERATION_NAMESPACE, constant(Soap.NS_CREDITO))
            .to("cxf:bean:buroCredito")
            .bean("soap", "leerCredito");

        // Transacción: descuenta existencias y registra el pedido; si algo falla, no queda nada a medias
        from("direct:registrarPedido").routeId("registrar-pedido")
            .transacted("PROPAGATION_REQUIRED")
            .to("sql:update inventario set existencias = existencias - :#cantidad where producto_id = :#productoId and existencias >= :#cantidad?" + DS)
            .choice().when(header(SqlConstants.SQL_UPDATE_COUNT).isEqualTo(0))
                .throwException(new Errores.Conflicto("Sin existencias suficientes")).end()
            .to("sql:insert into pedidos (cliente_id, producto_id, cantidad, total, estado, folio)"
                + " values (:#clienteId, :#productoId, :#cantidad, :#monto, 'PENDIENTE', :#folio) returning id?outputType=SelectOne&" + DS)
            .setHeader("pedidoId", body())
            .to("sql:insert into pedido_eventos (pedido_id, evento, detalle) values (:#pedidoId, 'CREADO', :#folio)?" + DS);

        // SOAP publicado: consultarPedido
        from("cxf:bean:pedidosSoap").routeId("soap-consultar-pedido")
            .bean("soap", "pedidoId")
            .to("sql:select id, estado, total, folio from pedidos where id = :#pedidoId?outputType=SelectOne&" + DS)
            .bean("soap", "respuestaPedido");
    }
}
