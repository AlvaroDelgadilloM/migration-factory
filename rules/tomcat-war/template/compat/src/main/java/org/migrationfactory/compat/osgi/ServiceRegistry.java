package org.migrationfactory.compat.osgi;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Sustituto minimo del registro de servicios OSGi.
 *
 * Los &lt;service&gt; de Blueprint se convierten en {@link ServiceExport} y los
 * &lt;reference&gt; en {@link ServiceImport}. La busqueda es por nombre exacto de interfaz
 * (y opcionalmente por nombre de componente), igual que en OSGi.
 */
public final class ServiceRegistry {

	private static final Map<String, Map<String, Object>> SERVICES = new ConcurrentHashMap<>();

	private ServiceRegistry() {
	}

	public static void register(String interfaceName, String componentName, Object service) {
		SERVICES.computeIfAbsent(interfaceName, k -> new ConcurrentHashMap<>()).put(componentName, service);
	}

	public static void unregister(String interfaceName, String componentName, Object service) {
		Map<String, Object> byName = SERVICES.get(interfaceName);
		if (byName != null) {
			byName.remove(componentName, service);
		}
	}

	public static Object lookup(String interfaceName, String componentName) {
		Map<String, Object> byName = SERVICES.get(interfaceName);
		if (byName == null || byName.isEmpty()) {
			throw new IllegalStateException("No hay ningun servicio exportado con la interfaz " + interfaceName);
		}
		if (componentName != null && !componentName.isEmpty()) {
			Object service = byName.get(componentName);
			if (service == null) {
				throw new IllegalStateException("No hay servicio '" + componentName + "' con la interfaz "
						+ interfaceName + ". Disponibles: " + byName.keySet());
			}
			return service;
		}
		if (byName.size() > 1) {
			throw new IllegalStateException("Hay " + byName.size() + " servicios con la interfaz " + interfaceName
					+ " " + byName.keySet() + "; indique component-name");
		}
		return byName.values().iterator().next();
	}
}
