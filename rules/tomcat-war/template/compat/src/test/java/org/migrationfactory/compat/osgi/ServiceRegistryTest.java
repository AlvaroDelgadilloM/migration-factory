package org.migrationfactory.compat.osgi;

import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

/** Misma semántica que el registro OSGi: interfaz exacta y, si hay varios, component-name. */
class ServiceRegistryTest {

	interface Rates {
	}

	@Test
	void looksUpByInterfaceAndComponentName() {
		String iface = Rates.class.getName() + ".a";
		Object only = new Object();
		ServiceRegistry.register(iface, "rates", only);
		assertSame(only, ServiceRegistry.lookup(iface, null));
		assertSame(only, ServiceRegistry.lookup(iface, "rates"));
		assertThrows(IllegalStateException.class, () -> ServiceRegistry.lookup(iface, "otro"));
		ServiceRegistry.unregister(iface, "rates", only);
		assertThrows(IllegalStateException.class, () -> ServiceRegistry.lookup(iface, null));
	}

	@Test
	void severalServicesOfOneInterfaceNeedAComponentName() {
		String iface = Rates.class.getName() + ".b";
		Object banxico = new Object();
		Object local = new Object();
		ServiceRegistry.register(iface, "banxico", banxico);
		ServiceRegistry.register(iface, "local", local);
		assertThrows(IllegalStateException.class, () -> ServiceRegistry.lookup(iface, null));
		assertSame(local, ServiceRegistry.lookup(iface, "local"));
	}
}
