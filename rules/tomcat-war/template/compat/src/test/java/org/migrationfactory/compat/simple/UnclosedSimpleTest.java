package org.migrationfactory.compat.simple;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.apache.camel.CamelContext;
import org.apache.camel.ExpressionIllegalSyntaxException;
import org.apache.camel.impl.DefaultCamelContext;
import org.apache.camel.support.DefaultExchange;
import org.junit.jupiter.api.Test;

/** Como Camel 2: la expresion sin cerrar no impide arrancar, pero falla al evaluarse. */
class UnclosedSimpleTest {

	@Test
	void failsWhenEvaluatedNotWhenBuilt() throws Exception {
		try (CamelContext context = new DefaultCamelContext()) {
			UnclosedSimple expression = UnclosedSimple.of("${exception.message");
			assertEquals("simple{${exception.message}", expression.toString());
			assertThrows(ExpressionIllegalSyntaxException.class, () -> expression.evaluate(new DefaultExchange(context), String.class));
		}
	}
}
