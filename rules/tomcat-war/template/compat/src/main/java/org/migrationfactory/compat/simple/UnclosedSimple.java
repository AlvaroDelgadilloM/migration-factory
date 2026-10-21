package org.migrationfactory.compat.simple;

import org.apache.camel.Exchange;
import org.apache.camel.ExpressionIllegalSyntaxException;
import org.apache.camel.support.ExpressionAdapter;

/**
 * Una expresion simple sin llave de cierre ({@code simple("${exception.message")}) tal como se comportaba en Camel 2:
 * compila y la ruta arranca, pero al evaluarse falla con {@link ExpressionIllegalSyntaxException}. Camel 4 la analiza al
 * arrancar y no levanta el contexto; cerrarla (lo que hacia la regla anterior) cambia la respuesta: en Posadas
 * common-catalogue respondia 400 donde el original responde 500, porque el error ocurria dentro del manejador de errores.
 * El reporte de migracion la senala como error latente del codigo original para que el cliente decida corregirla.
 */
public final class UnclosedSimple extends ExpressionAdapter {

	private final String text;

	private UnclosedSimple(String text) {
		this.text = text;
	}

	public static UnclosedSimple of(String text) {
		return new UnclosedSimple(text);
	}

	@Override
	public Object evaluate(Exchange exchange) {
		throw new ExpressionIllegalSyntaxException(text);
	}

	@Override
	public String toString() {
		return "simple{" + text + "}";
	}
}
