package org.migrationfactory.compat.spring;

import org.apache.camel.CamelContext;
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
		}
		return bean;
	}
}
