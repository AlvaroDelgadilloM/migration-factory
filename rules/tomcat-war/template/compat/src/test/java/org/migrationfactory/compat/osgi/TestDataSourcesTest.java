package org.migrationfactory.compat.osgi;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/** La base de prueba solo existe en una prueba automatica: en un despliegue el servicio faltante sigue siendo un error. */
class TestDataSourcesTest {

	@Test
	void withoutTheTestPropertyAMissingDataSourceIsStillAnError() {
		System.clearProperty("mf.test.jdbcUrl");
		assertFalse(TestDataSources.applies("com.mchange.v2.c3p0.PooledDataSource"));
		assertThrows(IllegalStateException.class, () -> ServiceRegistry.lookup("com.mchange.v2.c3p0.PooledDataSource.sinExportar", "dsUC"));
	}

	@Test
	void inATestOnlyDataSourcesAreResolved() {
		System.setProperty("mf.test.jdbcUrl", "jdbc:h2:mem:prueba");
		try {
			assertTrue(TestDataSources.applies("com.mchange.v2.c3p0.PooledDataSource"));
			assertTrue(TestDataSources.applies("javax.sql.DataSource"));
			assertFalse(TestDataSources.applies("com.posadas.RatesService"));
			assertThrows(IllegalStateException.class, () -> ServiceRegistry.lookup("com.posadas.RatesService", null));
		} finally {
			System.clearProperty("mf.test.jdbcUrl");
		}
	}
}
