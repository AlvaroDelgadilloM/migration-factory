package demo.pedidos;

import java.io.StringReader;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Map;

import javax.xml.parsers.DocumentBuilderFactory;

import org.apache.camel.Exchange;
import org.apache.camel.Header;
import org.apache.camel.component.cxf.CxfPayload;
import org.apache.cxf.binding.soap.SoapFault;
import org.apache.cxf.binding.soap.SoapHeader;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;
import org.xml.sax.InputSource;

/** Arma y lee los mensajes SOAP en modo PAYLOAD: la petición al buró de crédito y la consulta de pedidos que publica el servicio. */
public class Soap {

    static final String NS_CREDITO = "http://proveedor.example/credito";
    static final String NS_PEDIDOS = "http://demo.example/pedidos";

    public CxfPayload<SoapHeader> peticionCredito(@Header("clienteId") Integer clienteId, @Header("monto") BigDecimal monto) {
        return payload("<c:evaluar xmlns:c=\"" + NS_CREDITO + "\"><c:clienteId>" + clienteId + "</c:clienteId><c:monto>"
                + monto.toPlainString() + "</c:monto></c:evaluar>");
    }

    /** Deja el folio del buró en el header folio; un "no" del buró es CreditoRechazado. */
    public void leerCredito(Exchange exchange) {
        Element respuesta = primero(exchange);
        if (!"true".equals(texto(respuesta, NS_CREDITO, "aprobado"))) {
            throw new Errores.CreditoRechazado("El buró no aprobó el crédito");
        }
        exchange.getIn().setHeader("folio", texto(respuesta, NS_CREDITO, "folio"));
    }

    /** Del mensaje consultarPedido al header pedidoId. */
    public void pedidoId(Exchange exchange) {
        String id = texto(primero(exchange), NS_PEDIDOS, "pedidoId");
        exchange.getIn().setHeader("pedidoId", Integer.valueOf(id.trim()));
    }

    /** La fila del pedido como consultarPedidoResponse; sin fila, un SOAP Fault para el cliente. */
    public CxfPayload<SoapHeader> respuestaPedido(Exchange exchange) {
        @SuppressWarnings("unchecked")
        Map<String, Object> fila = exchange.getIn().getBody(Map.class);
        if (fila == null) {
            throw new SoapFault("Pedido no encontrado", SoapFault.FAULT_CODE_CLIENT);
        }
        Object folio = fila.get("folio");
        return payload("<p:consultarPedidoResponse xmlns:p=\"" + NS_PEDIDOS + "\"><p:pedidoId>" + fila.get("id") + "</p:pedidoId><p:estado>"
                + fila.get("estado") + "</p:estado><p:total>" + fila.get("total") + "</p:total>"
                + (folio == null ? "" : "<p:folio>" + folio + "</p:folio>") + "</p:consultarPedidoResponse>");
    }

    @SuppressWarnings("deprecation")
    static CxfPayload<SoapHeader> payload(String xml) {
        try {
            DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
            f.setNamespaceAware(true);
            Document doc = f.newDocumentBuilder().parse(new InputSource(new StringReader(xml)));
            return new CxfPayload<SoapHeader>(new ArrayList<SoapHeader>(), Collections.singletonList(doc.getDocumentElement()));
        } catch (Exception e) {
            throw new IllegalStateException("XML inválido: " + xml, e);
        }
    }

    private static Element primero(Exchange exchange) {
        CxfPayload<?> payload = exchange.getIn().getBody(CxfPayload.class);
        return payload.getBody().get(0);
    }

    static String texto(Element e, String ns, String nombre) {
        NodeList n = e.getElementsByTagNameNS(ns, nombre);
        return n.getLength() == 0 ? null : n.item(0).getTextContent();
    }
}
