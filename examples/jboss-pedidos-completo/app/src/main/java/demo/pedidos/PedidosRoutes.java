package demo.pedidos;

import java.math.BigDecimal;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.apache.camel.Exchange;
import org.apache.camel.ExchangePattern;
import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.component.sql.SqlConstants;
import org.apache.camel.model.dataformat.JsonLibrary;
import org.apache.camel.model.rest.RestBindingMode;
import org.apache.cxf.binding.soap.SoapFault;

import com.fasterxml.jackson.databind.ObjectMapper;

/** Servicios REST del pedido: cada error de negocio se traduce aquí a su código HTTP (onException del RouteBuilder). */
public class PedidosRoutes extends RouteBuilder {

    static final String DS = "dataSource=#pedidosDS";
    private static final ObjectMapper JSON = new ObjectMapper();

    @Override
    public void configure() {
        restConfiguration().component("servlet").bindingMode(RestBindingMode.off);

        onException(Errores.Validacion.class).handled(true).process(e -> responder(e, 400, "VALIDACION", null));
        onException(Errores.NoEncontrado.class).handled(true).process(e -> responder(e, 404, "NO_ENCONTRADO", null));
        onException(Errores.Conflicto.class).handled(true).process(e -> responder(e, 409, "SIN_EXISTENCIAS", null));
        onException(Errores.ClienteInactivo.class).handled(true).process(e -> responder(e, 422, "CLIENTE_INACTIVO", null));
        onException(SoapFault.class, Errores.CreditoRechazado.class).handled(true).process(e -> responder(e, 422, "CREDITO_RECHAZADO", null));
        // el buró no responde: un reintento y, si sigue igual, 503 y el aviso a la cola de errores
        onException(SocketTimeoutException.class, ConnectException.class).maximumRedeliveries(1).redeliveryDelay(300).handled(true)
            .process(e -> responder(e, 503, "CREDITO_NO_DISPONIBLE", "El buró de crédito no respondió"))
            .wireTap("direct:avisarErrorCredito");
        onException(Exception.class).handled(true).process(e -> responder(e, 500, "ERROR_INTERNO", "No se pudo registrar la operación"));

        rest("/clientes").get("/{id}").to("direct:cliente");
        rest("/productos").get().to("direct:productos");
        rest("/pedidos")
            .post().to("direct:crearPedido")
            .get("/{id}").to("direct:pedido")
            .post("/{id}/cancelar").to("direct:cancelar")
            .post("/confirmaciones").to("direct:encolar");
        rest("/eventos").get().to("direct:eventos");
        rest("/reportes").get("/inventario").to("direct:reporteInventario");

        from("direct:cliente").routeId("cliente")
            .to("sql:select id, nombre, email, limite_credito as \"limiteCredito\", activo from clientes where id = cast(:#id as integer)?outputType=SelectOne&" + DS)
            .choice().when(body().isNull()).throwException(new Errores.NoEncontrado("Cliente no encontrado")).end()
            .to("direct:json");

        from("direct:productos").routeId("productos")
            .choice()
                .when(header("categoria").isNotNull())
                    .to("sql:select p.id, p.nombre, p.categoria, p.precio, i.existencias from productos p join inventario i on i.producto_id = p.id"
                        + " where p.categoria = :#categoria order by p.id?" + DS)
                .otherwise()
                    .to("sql:select p.id, p.nombre, p.categoria, p.precio, i.existencias from productos p join inventario i on i.producto_id = p.id"
                        + " order by p.id?" + DS)
            .end()
            .to("direct:json");

        from("direct:crearPedido").routeId("crear-pedido")
            .unmarshal().json(JsonLibrary.Jackson, Map.class)
            .bean("validador", "validar")
            .to("sql:select activo from clientes where id = :#clienteId?outputType=SelectOne&" + DS)
            .choice()
                .when(body().isNull()).throwException(new Errores.NoEncontrado("Cliente no encontrado"))
                .when(simple("${body} == false")).throwException(new Errores.ClienteInactivo("El cliente está inactivo"))
            .end()
            .to("sql:select precio from productos where id = :#productoId?outputType=SelectOne&" + DS)
            .choice().when(body().isNull()).throwException(new Errores.NoEncontrado("Producto no encontrado")).end()
            .process(e -> e.getIn().setHeader("monto",
                e.getIn().getBody(BigDecimal.class).multiply(BigDecimal.valueOf(e.getIn().getHeader("cantidad", Integer.class)))))
            .to("direct:evaluarCredito")
            .to("direct:registrarPedido")
            .setBody(header("pedidoId")).convertBodyTo(String.class)
            .to(ExchangePattern.InOnly, "activemq:queue:pedidos.confirmados")
            .process(e -> {
                Map<String, Object> r = new LinkedHashMap<>();
                r.put("pedidoId", e.getIn().getHeader("pedidoId"));
                r.put("estado", "PENDIENTE");
                r.put("total", e.getIn().getHeader("monto"));
                r.put("folio", e.getIn().getHeader("folio"));
                json(e, 201, r);
            });

        from("direct:pedido").routeId("pedido")
            .to("sql:select id, cliente_id as \"clienteId\", producto_id as \"productoId\", cantidad, total, estado, folio from pedidos"
                + " where id = cast(:#id as integer)?outputType=SelectOne&" + DS)
            .choice().when(body().isNull()).throwException(new Errores.NoEncontrado("Pedido no encontrado")).end()
            .to("direct:json");

        from("direct:cancelar").routeId("cancelar-pedido")
            .doTry()
                .to("sql:update pedidos set estado = 'CANCELADO' where id = cast(:#id as integer) and estado in ('PENDIENTE', 'CONFIRMADO')?" + DS)
                .choice().when(header(SqlConstants.SQL_UPDATE_COUNT).isEqualTo(0))
                    .throwException(new Errores.Conflicto("El pedido no se puede cancelar en su estado actual")).end()
                .setHeader(Exchange.HTTP_RESPONSE_CODE, constant(200))
                .setBody(simple("{\"pedidoId\":${header.id},\"estado\":\"CANCELADO\"}"))
            .endDoTry()
            .doCatch(Errores.Conflicto.class)
                .setHeader(Exchange.HTTP_RESPONSE_CODE, constant(409))
                .setBody(simple("{\"error\":\"NO_CANCELABLE\",\"detalle\":\"${exception.message}\"}"))
            .doFinally()
                .setHeader("respuesta", body())
                .setHeader("detalle", simple("HTTP ${header.CamelHttpResponseCode}"))
                .to("sql:insert into pedido_eventos (pedido_id, evento, detalle) values (cast(:#id as integer), 'CANCELACION', :#detalle)?" + DS)
                .setBody(header("respuesta"))
            .end()
            .setHeader(Exchange.CONTENT_TYPE, constant("application/json"));

        from("direct:encolar").routeId("encolar-confirmacion")
            .convertBodyTo(String.class)
            .to(ExchangePattern.InOnly, "activemq:queue:pedidos.confirmados")
            .process(e -> json(e, 202, java.util.Collections.singletonMap("encolado", true)));

        from("direct:eventos").routeId("eventos")
            .to("sql:select pedido_id as \"pedidoId\", evento, detalle from pedido_eventos where evento = :#tipo order by id?" + DS)
            .to("direct:json");

        from("direct:reporteInventario").routeId("reporte-inventario")
            .to("sql:select p.id, p.nombre, i.existencias, p.precio * i.existencias as valor from productos p join inventario i on i.producto_id = p.id?" + DS)
            .process(e -> {
                StringBuilder xml = new StringBuilder("<inventario>");
                for (Object o : e.getIn().getBody(List.class)) {
                    Map<?, ?> f = (Map<?, ?>) o;
                    xml.append("<producto id=\"").append(f.get("id")).append("\" existencias=\"").append(f.get("existencias"))
                       .append("\" valor=\"").append(f.get("valor")).append("\" nombre=\"").append(escapar(String.valueOf(f.get("nombre")))).append("\"/>");
                }
                e.getIn().setBody(xml.append("</inventario>").toString());
            })
            .to("xslt:xslt/inventario.xsl")
            .setHeader(Exchange.CONTENT_TYPE, constant("application/xml"));

        from("direct:json").routeId("a-json")
            .marshal().json(JsonLibrary.Jackson)
            .setHeader(Exchange.CONTENT_TYPE, constant("application/json"));

        from("direct:avisarErrorCredito").routeId("avisar-error-credito")
            .setBody(simple("{\"clienteId\":${header.clienteId},\"monto\":${header.monto},\"error\":\"CREDITO_NO_DISPONIBLE\"}"))
            .to(ExchangePattern.InOnly, "activemq:queue:pedidos.errores");
    }

    /** Respuesta de error: {error, detalle} con el código HTTP; detalle = el mensaje de la excepción si no se da uno fijo. */
    static void responder(Exchange e, int codigo, String error, String detalle) {
        Exception causa = e.getProperty(Exchange.EXCEPTION_CAUGHT, Exception.class);
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("error", error);
        r.put("detalle", detalle != null ? detalle : causa == null ? null : causa.getMessage());
        json(e, codigo, r);
    }

    static void json(Exchange e, int codigo, Object cuerpo) {
        try {
            e.getIn().setBody(JSON.writeValueAsString(cuerpo));
        } catch (Exception ex) {
            throw new IllegalStateException(ex);
        }
        e.getIn().setHeader(Exchange.HTTP_RESPONSE_CODE, codigo);
        e.getIn().setHeader(Exchange.CONTENT_TYPE, "application/json");
    }

    static String escapar(String s) {
        return s.replace("&", "&amp;").replace("\"", "&quot;").replace("<", "&lt;").replace(">", "&gt;");
    }
}
