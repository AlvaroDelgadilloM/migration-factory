package org.migrationfactory.compat.xmljson;

import java.io.InputStream;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.util.List;

import org.apache.camel.Exchange;
import org.apache.camel.StreamCache;
import org.apache.camel.spi.DataFormat;
import org.apache.camel.support.ExchangeHelper;
import org.apache.camel.support.service.ServiceSupport;

import net.sf.json.JSON;
import net.sf.json.JSONSerializer;
import net.sf.json.xml.XMLSerializer;

/**
 * Reemplazo del dataformat xmljson (camel-xmljson), eliminado en Camel 3.
 *
 * Usa la misma libreria (json-lib) y las mismas opciones que la version de Camel 2.23, de modo
 * que el JSON/XML generado conserva la forma actual. Se registra como bean con el mismo id que
 * tenia el elemento &lt;xmljson id="..."/&gt; y las rutas siguen usando .marshal("xmljson").
 *
 * marshal = XML a JSON, unmarshal = JSON a XML.
 */
public class XmlJsonDataFormat extends ServiceSupport implements DataFormat {

	private XMLSerializer serializer;

	private String encoding;
	private String elementName;
	private String arrayName;
	private String rootName;
	private Boolean forceTopLevelObject;
	private Boolean namespaceLenient;
	private Boolean skipWhitespace;
	private Boolean trimSpaces;
	private Boolean skipNamespaces;
	private Boolean removeNamespacePrefixes;
	private List<String> expandableProperties;
	private String typeHints;

	@Override
	protected void doStart() throws Exception {
		XMLSerializer s = new XMLSerializer();
		if (forceTopLevelObject != null) {
			s.setForceTopLevelObject(forceTopLevelObject);
		}
		if (namespaceLenient != null) {
			s.setNamespaceLenient(namespaceLenient);
		}
		if (rootName != null) {
			s.setRootName(rootName);
		}
		if (elementName != null) {
			s.setElementName(elementName);
		}
		if (arrayName != null) {
			s.setArrayName(arrayName);
		}
		if (expandableProperties != null && !expandableProperties.isEmpty()) {
			s.setExpandableProperties(expandableProperties.toArray(new String[0]));
		}
		if (skipWhitespace != null) {
			s.setSkipWhitespace(skipWhitespace);
		}
		if (trimSpaces != null) {
			s.setTrimSpaces(trimSpaces);
		}
		if (skipNamespaces != null) {
			s.setSkipNamespaces(skipNamespaces);
		}
		if (removeNamespacePrefixes != null) {
			s.setRemoveNamespacePrefixFromElements(removeNamespacePrefixes);
		}
		if ("YES".equalsIgnoreCase(typeHints) || "WITH_PREFIX".equalsIgnoreCase(typeHints)) {
			s.setTypeHintsEnabled(true);
			s.setTypeHintsCompatibility(!"WITH_PREFIX".equalsIgnoreCase(typeHints));
		} else {
			s.setTypeHintsEnabled(false);
			s.setTypeHintsCompatibility(false);
		}
		serializer = s;
	}

	@Override
	protected void doStop() throws Exception {
		serializer = null;
	}

	@Override
	public void marshal(Exchange exchange, Object graph, OutputStream stream) throws Exception {
		// as camel-xmljson 2.23: a String body is read as text, anything else as a stream when it converts to one
		InputStream xml = graph instanceof String ? null
				: exchange.getContext().getTypeConverter().convertTo(InputStream.class, exchange, graph);
		JSON json;
		if (xml != null) {
			json = serializer.readFromStream(xml);
		} else {
			String text = exchange.getContext().getTypeConverter().mandatoryConvertTo(String.class, exchange, graph);
			json = serializer.read(text);
		}
		OutputStreamWriter writer = new OutputStreamWriter(stream, ExchangeHelper.getCharsetName(exchange));
		json.write(writer);
		writer.flush();
	}

	@Override
	public Object unmarshal(Exchange exchange, InputStream stream) throws Exception {
		Object body = exchange.getIn().getBody();
		// as camel-xmljson 2.23 (bytecode): an empty request (no body, an empty stream cache or empty text) is not JSON to
		// parse; it stays empty and the next step decides (e.g. the schema validator answers its own 400). Parsing it threw
		// "Invalid JSON String" and turned that 400 into a 500.
		if (body == null) {
			return null;
		}
		if (body instanceof StreamCache cache && cache.length() <= 0) {
			return body;
		}
		JSON json;
		if (body instanceof JSON) {
			json = (JSON) body;
		} else {
			String text = exchange.getContext().getTypeConverter().convertTo(String.class, exchange, body);
			if (text == null || text.isEmpty()) {
				return null;
			}
			json = JSONSerializer.toJSON(text);
		}
		return encoding == null ? serializer.write(json) : serializer.write(json, encoding);
	}

	public void setEncoding(String encoding) {
		this.encoding = encoding;
	}

	public void setElementName(String elementName) {
		this.elementName = elementName;
	}

	public void setArrayName(String arrayName) {
		this.arrayName = arrayName;
	}

	public void setRootName(String rootName) {
		this.rootName = rootName;
	}

	public void setForceTopLevelObject(Boolean forceTopLevelObject) {
		this.forceTopLevelObject = forceTopLevelObject;
	}

	public void setNamespaceLenient(Boolean namespaceLenient) {
		this.namespaceLenient = namespaceLenient;
	}

	public void setSkipWhitespace(Boolean skipWhitespace) {
		this.skipWhitespace = skipWhitespace;
	}

	public void setTrimSpaces(Boolean trimSpaces) {
		this.trimSpaces = trimSpaces;
	}

	public void setSkipNamespaces(Boolean skipNamespaces) {
		this.skipNamespaces = skipNamespaces;
	}

	public void setRemoveNamespacePrefixes(Boolean removeNamespacePrefixes) {
		this.removeNamespacePrefixes = removeNamespacePrefixes;
	}

	public void setExpandableProperties(List<String> expandableProperties) {
		this.expandableProperties = expandableProperties;
	}

	public void setTypeHints(String typeHints) {
		this.typeHints = typeHints;
	}
}
