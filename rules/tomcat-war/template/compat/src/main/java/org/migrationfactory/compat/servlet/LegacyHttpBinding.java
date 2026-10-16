package org.migrationfactory.compat.servlet;

import java.io.ByteArrayInputStream;
import java.io.IOException;

import org.apache.camel.Message;
import org.apache.camel.http.common.DefaultHttpBinding;

import jakarta.servlet.http.HttpServletRequest;

/**
 * Una peticion sin cuerpo (GET) llegaba a las rutas de Camel 2 con un cuerpo vacio, no nulo. Camel 4 entrega null.
 *
 * La diferencia cambia el resultado de rutas que preguntan body().isNull() despues de un paso que no reemplaza el
 * cuerpo (por ejemplo una consulta SQL sin filas): con null toman otra rama. Este binding conserva el cuerpo vacio de
 * Camel 2 para que las rutas sigan el mismo camino que hoy.
 */
public class LegacyHttpBinding extends DefaultHttpBinding {

	@Override
	public Object parseBody(HttpServletRequest request, Message message) throws IOException {
		Object body = super.parseBody(request, message);
		return body != null ? body : new ByteArrayInputStream(new byte[0]);
	}
}
