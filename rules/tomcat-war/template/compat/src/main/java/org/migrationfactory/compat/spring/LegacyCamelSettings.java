package org.migrationfactory.compat.spring;

import org.apache.camel.CamelContext;
import org.apache.camel.Exchange;
import org.apache.camel.Processor;
import org.apache.camel.model.ModelCamelContext;
import org.apache.camel.model.ModelLifecycleStrategySupport;
import org.apache.camel.model.ProcessDefinition;
import org.apache.camel.model.RouteDefinition;
import org.apache.camel.model.rest.RestBindingDefinition;
import org.apache.camel.spring.xml.CamelContextFactoryBean;
import org.springframework.beans.factory.config.BeanPostProcessor;

/**
 * Ajustes que cada CamelContext necesita para comportarse como en Camel 2, aplicados antes de
 * que se construyan las rutas y sin tocar los RouteBuilder.
 */
public class LegacyCamelSettings implements BeanPostProcessor {

	@Override
	public Object postProcessAfterInitialization(Object bean, String beanName) {
		if (bean instanceof CamelContextFactoryBean) {
			CamelContext context = ((CamelContextFactoryBean) bean).getContext();
			// Camel 4 "inserta" en el servicio REST la ruta direct a la que apunta y por eso exige
			// que cada verbo use un direct distinto. Varias rutas actuales comparten el mismo
			// direct entre verbos; se vuelve al comportamiento de Camel 2 (rutas enlazadas).
			context.getRestConfiguration().setInlineRoutes(false);
			((ModelCamelContext) context).addModelLifecycleStrategy(new ProducesContentType());
		}
		return bean;
	}

	/**
	 * Content-Type de las respuestas REST que la ruta no fija. Camel 2 usaba primero el `produces` del servicio;
	 * Camel 4 mira primero el header Accept de la peticion. Con produces("application/json") y Accept: application/xml
	 * Camel 2 responde application/json y Camel 4 application/xml, con el mismo cuerpo. Se agrega un paso al final de
	 * cada ruta REST que deja el valor de Camel 2 antes de que Camel 4 aplique el suyo.
	 */
	static final class ProducesContentType extends ModelLifecycleStrategySupport {

		@Override
		public void onAddRouteDefinition(RouteDefinition route) {
			RestBindingDefinition binding = route.getRestBindingDefinition();
			String produces = binding == null ? null : binding.getProduces();
			if (produces == null || produces.isEmpty()) {
				return;
			}
			boolean skipOnError = binding.getSkipBindingOnErrorCode() == null || Boolean.parseBoolean(binding.getSkipBindingOnErrorCode());
			Processor step = exchange -> {
				Integer code = exchange.getMessage().getHeader(Exchange.HTTP_RESPONSE_CODE, Integer.class);
				if (skipOnError && code != null && code >= 300) {
					return;
				}
				if (exchange.getMessage().getHeader(Exchange.CONTENT_TYPE) == null) {
					exchange.getMessage().setHeader(Exchange.CONTENT_TYPE, produces);
				}
			};
			route.getOutputs().add(new ProcessDefinition(step));
		}
	}
}
