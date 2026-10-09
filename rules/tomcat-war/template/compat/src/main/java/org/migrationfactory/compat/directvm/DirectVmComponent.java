package org.migrationfactory.compat.directvm;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

import org.apache.camel.Endpoint;
import org.apache.camel.support.DefaultComponent;

/**
 * Reemplazo del componente direct-vm, eliminado en Camel 4.
 *
 * Permite llamadas sincronas entre rutas de distintos CamelContext dentro de la misma JVM,
 * que es como se comunican hoy los bundles en Karaf. Los consumidores se guardan en un mapa
 * estatico, por lo que todos los modulos deben cargarse con el mismo classloader (un solo WAR).
 *
 * Camel lo descubre por META-INF/services/org/apache/camel/component/direct-vm.
 */
public class DirectVmComponent extends DefaultComponent {

	private static final ConcurrentMap<String, DirectVmConsumer> CONSUMERS = new ConcurrentHashMap<>();

	private boolean block = true;
	private long timeout = 30000L;

	@Override
	protected Endpoint createEndpoint(String uri, String remaining, Map<String, Object> parameters) throws Exception {
		DirectVmEndpoint endpoint = new DirectVmEndpoint(uri, this, remaining);
		endpoint.setBlock(getAndRemoveParameter(parameters, "block", boolean.class, block));
		endpoint.setTimeout(getAndRemoveParameter(parameters, "timeout", long.class, timeout));
		endpoint.setFailIfNoConsumers(getAndRemoveParameter(parameters, "failIfNoConsumers", boolean.class, true));
		endpoint.setPropagateProperties(getAndRemoveParameter(parameters, "propagateProperties", boolean.class, true));
		return endpoint;
	}

	static void addConsumer(String name, DirectVmConsumer consumer) {
		DirectVmConsumer existing = CONSUMERS.putIfAbsent(name, consumer);
		if (existing != null && existing != consumer) {
			throw new IllegalStateException("Ya existe un consumidor para direct-vm:" + name + " en el contexto "
					+ existing.getEndpoint().getCamelContext().getName());
		}
	}

	static void removeConsumer(String name, DirectVmConsumer consumer) {
		CONSUMERS.remove(name, consumer);
	}

	static DirectVmConsumer getConsumer(String name) {
		return CONSUMERS.get(name);
	}

	public void setBlock(boolean block) {
		this.block = block;
	}

	public void setTimeout(long timeout) {
		this.timeout = timeout;
	}
}
