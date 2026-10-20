package org.migrationfactory.compat.servlet;

import java.io.IOException;

import org.apache.camel.Message;
import org.apache.camel.http.common.DefaultHttpBinding;

import jakarta.servlet.http.HttpServletRequest;

/**
 * Cuerpo de una peticion que declara Content-Length: 0 (un DELETE o un POST sin datos): Camel 2 lo dejaba en null
 * (DefaultHttpBinding.parseBody de camel-http-common 2.23: "if (contentLength == 0) return null"); Camel 4 entrega un
 * cuerpo vacio. Una ruta que convierte el cuerpo (xmljson, por ejemplo) falla distinto con vacio que con null: con null
 * json-lib produce un documento vacio y la validacion XSD responde 400; con vacio responde "Invalid JSON String" (500).
 */
public class LegacyHttpBinding extends DefaultHttpBinding {

	@Override
	public Object parseBody(HttpServletRequest request, Message message) throws IOException {
		return request.getContentLength() == 0 ? null : super.parseBody(request, message);
	}
}
