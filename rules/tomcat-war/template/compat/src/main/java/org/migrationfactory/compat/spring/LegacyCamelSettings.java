package org.migrationfactory.compat.spring;

import java.util.List;

import org.apache.camel.CamelContext;
import org.apache.camel.Exchange;
import org.apache.camel.Message;
import org.apache.camel.Processor;
import org.apache.camel.model.ChoiceDefinition;
import org.apache.camel.model.ModelCamelContext;
import org.apache.camel.model.ModelLifecycleStrategySupport;
import org.apache.camel.model.ProcessDefinition;
import org.apache.camel.model.ProcessorDefinition;
import org.apache.camel.model.RouteDefinition;
import org.apache.camel.model.ToDefinition;
import org.apache.camel.model.WhenDefinition;
import org.apache.camel.model.rest.RestBindingDefinition;
import org.apache.camel.spring.xml.CamelContextFactoryBean;
import org.springframework.beans.factory.config.BeanPostProcessor;

/**
 * Ajustes que cada CamelContext necesita para comportarse como en Camel 2, aplicados antes de
 * que se construyan las rutas y sin tocar los RouteBuilder.
 */
public class LegacyCamelSettings implements BeanPostProcessor {

	private final boolean consumersOff;

	public LegacyCamelSettings() {
		this(false);
	}

	/** consumersOff: el modulo arranca sin sus colas ni tareas programadas (modo interno de una ola, o mf.consumers.off). */
	public LegacyCamelSettings(boolean consumersOff) {
		this.consumersOff = consumersOff;
	}

	@Override
	public Object postProcessAfterInitialization(Object bean, String beanName) {
		if (bean instanceof CamelContextFactoryBean) {
			CamelContextFactoryBean factory = (CamelContextFactoryBean) bean;
			CamelContext context = factory.getContext();
			// Stream caching: apagado por omision en Camel 2, encendido desde Camel 3. Con el encendido, el cuerpo de una
			// peticion que la ruta ya leyo vuelve a estar disponible y, si la ruta no fija respuesta, se devuelve la peticion
			// en lugar de un cuerpo vacio. Se respeta lo que el XML declare (streamCache="true") y el de cada ruta.
			if (factory.getStreamCache() == null) {
				context.setStreamCaching(false);
			}
			// Camel 4 "inserta" en el servicio REST la ruta direct a la que apunta y por eso exige
			// que cada verbo use un direct distinto. Varias rutas actuales comparten el mismo
			// direct entre verbos; se vuelve al comportamiento de Camel 2 (rutas enlazadas).
			context.getRestConfiguration().setInlineRoutes(false);
			((ModelCamelContext) context).addModelLifecycleStrategy(new ProducesContentType());
			((ModelCamelContext) context).addModelLifecycleStrategy(new CxfOperationHeaders());
			context.addLifecycleStrategy(new NoAutomaticHttpRetries());
			if (consumersOff) {
				((ModelCamelContext) context).addModelLifecycleStrategy(new PassiveConsumers(context));
			}
		}
		return bean;
	}

	/**
	 * Reintentos HTTP automaticos. El cliente de Camel 4 (Apache HttpClient 5) repite por su cuenta una llamada que recibe
	 * 503 o 429, despues de esperar 1 segundo; el de Camel 2 (HttpClient 4, componente http4) no lo hacia. Con un sistema
	 * externo saturado eso suma un segundo y duplica las llamadas (Posadas: las consultas de reservas de Upsell Central a
	 * ICP pasaban de milisegundos a 1 s). Se desactivan al crear el componente http/https, conservando el configurador
	 * que el modulo hubiera puesto.
	 */
	static final class NoAutomaticHttpRetries extends org.apache.camel.support.LifecycleStrategySupport {

		@Override
		public void onComponentAdd(String name, org.apache.camel.Component component) {
			if (component instanceof org.apache.camel.component.http.HttpComponent) {
				org.apache.camel.component.http.HttpComponent http = (org.apache.camel.component.http.HttpComponent) component;
				org.apache.camel.component.http.HttpClientConfigurer own = http.getHttpClientConfigurer();
				http.setHttpClientConfigurer(builder -> {
					if (own != null) {
						own.configureHttpClient(builder);
					}
					builder.disableAutomaticRetries();
				});
			}
		}
	}

	/**
	 * Despliegue por olas: las rutas que consumen por su cuenta (colas, temporizadores, tareas programadas, archivos, sondeo de
	 * base de datos) no arrancan; las que responden a una llamada (REST, SOAP, direct, direct-vm) si. Asi un modulo puede
	 * correr en Tomcat sin procesar dos veces lo que el sistema actual todavia procesa.
	 */
	static final class PassiveConsumers extends ModelLifecycleStrategySupport {

		static final java.util.Set<String> SCHEMES = java.util.Set.of("activemq", "jms", "amqp", "sjms", "sjms2", "stomp", "mqtt", "paho",
				"kafka", "rabbitmq", "spring-rabbitmq", "timer", "quartz", "quartz2", "scheduler", "cron", "file", "ftp", "ftps", "sftp", "sql",
				"jpa", "mybatis", "imap", "imaps", "pop3", "pop3s");

		private static final org.slf4j.Logger LOG = org.slf4j.LoggerFactory.getLogger(PassiveConsumers.class);

		private final CamelContext context;

		PassiveConsumers(CamelContext context) {
			this.context = context;
		}

		@Override
		public void onAddRouteDefinition(RouteDefinition route) {
			String from = route.getInput() == null ? null : route.getInput().getEndpointUri();
			if (from != null && from.contains("{{")) {
				try {
					from = context.resolvePropertyPlaceholders(from);
				} catch (RuntimeException e) {
					LOG.warn("Ruta {}: no se pudo resolver {} para saber si consume de una cola o tarea", route.getRouteId(), from);
				}
			}
			int colon = from == null ? -1 : from.indexOf(':');
			if (colon > 0 && SCHEMES.contains(from.substring(0, colon))) {
				route.setAutoStartup("false");
				LOG.info("Ruta {} sin arrancar: consume de {} y su ola aun no la procesa aqui", route.getRouteId(), from.substring(0, colon));
			}
		}
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

	/**
	 * Nombre de la operacion SOAP. Camel 2 usaba los headers "operationName" y "operationNamespace" (CxfConstants); desde
	 * Camel 3 son "CamelCxfOperationName" y "CamelCxfOperationNamespace". El codigo actual lee y escribe los nombres de
	 * Camel 2: un toD("direct:${header.operationName}") recibia vacio y una llamada cxf: con setHeader("operationName", X)
	 * no elegia la operacion X, sin ningun error. Se mantienen los dos nombres: al entrar por un endpoint cxf: se copian
	 * los de Camel 4 a los de Camel 2, y justo antes de cada llamada a cxf: los de Camel 2 a los de Camel 4.
	 */
	static final class CxfOperationHeaders extends ModelLifecycleStrategySupport {

		static final String[][] NAMES = { { "CamelCxfOperationName", "operationName" }, { "CamelCxfOperationNamespace", "operationNamespace" } };

		@Override
		public void onAddRouteDefinition(RouteDefinition route) {
			String from = route.getInput() == null ? null : route.getInput().getEndpointUri();
			if (from != null && from.startsWith("cxf:")) {
				route.getOutputs().add(0, new ProcessDefinition(exchange -> copy(exchange.getIn(), 0, 1)));
			}
			beforeCxfCalls(route.getOutputs());
		}

		private static void beforeCxfCalls(List<ProcessorDefinition<?>> outputs) {
			for (int i = 0; i < outputs.size(); i++) {
				ProcessorDefinition<?> p = outputs.get(i);
				if (p instanceof ToDefinition && ((ToDefinition) p).getUri() != null && ((ToDefinition) p).getUri().startsWith("cxf:")) {
					outputs.add(i, new ProcessDefinition(exchange -> copy(exchange.getIn(), 1, 0)));
					i++;
					continue;
				}
				if (p instanceof ChoiceDefinition) {
					for (WhenDefinition w : ((ChoiceDefinition) p).getWhenClauses()) {
						beforeCxfCalls(w.getOutputs());
					}
					if (((ChoiceDefinition) p).getOtherwise() != null) {
						beforeCxfCalls(((ChoiceDefinition) p).getOtherwise().getOutputs());
					}
				}
				beforeCxfCalls(p.getOutputs());
			}
		}

		/** Copia cada header del nombre `from` al nombre `to` cuando el primero existe. */
		static void copy(Message in, int from, int to) {
			for (String[] pair : NAMES) {
				Object value = in.getHeader(pair[from]);
				if (value != null) {
					in.setHeader(pair[to], value);
				}
			}
		}
	}
}
