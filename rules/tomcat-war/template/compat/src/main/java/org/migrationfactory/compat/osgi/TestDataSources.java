package org.migrationfactory.compat.osgi;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

/**
 * Solo para las pruebas automaticas (-Dmf.test.jdbcUrl=...): un datasource que ningun modulo exporta (en el servidor del
 * cliente lo publica otro bundle que no esta en el repositorio) se resuelve con la base de pruebas H2 indicada, en lugar
 * de impedir que el modulo arranque. Sin esa propiedad no hace nada: en produccion el servicio faltante sigue siendo un
 * error. El original (Camel 2) y el migrado usan esta misma clase, asi que los dos ven la misma base.
 *
 * Compila con Java 8 y no depende de H2 en tiempo de compilacion: el servidor de pruebas pone el driver en el classpath.
 */
final class TestDataSources {

	private TestDataSources() {
	}

	static boolean applies(String interfaceName) {
		String url = System.getProperty("mf.test.jdbcUrl", "");
		return !url.isEmpty() && interfaceName != null && interfaceName.endsWith("DataSource");
	}

	static Object create(String interfaceName, ClassLoader loader) {
		try {
			Class<?> h2 = loader.loadClass("org.h2.jdbcx.JdbcDataSource");
			Object target = h2.getConstructor().newInstance();
			h2.getMethod("setURL", String.class).invoke(target, System.getProperty("mf.test.jdbcUrl"));
			Class<?> type = loader.loadClass(interfaceName);
			// la interfaz pedida puede ser de un pool (p. ej. PooledDataSource de c3p0): lo que tambien es DataSource se
			// atiende con H2; el resto devuelve el valor neutro de su tipo
			return Proxy.newProxyInstance(loader, new Class<?>[] { type, javax.sql.DataSource.class }, (proxy, method, args) -> {
				if (method.getDeclaringClass() == Object.class) {
					switch (method.getName()) {
					case "toString":
						return "DataSource de prueba (" + interfaceName + ")";
					case "hashCode":
						return System.identityHashCode(proxy);
					case "equals":
						return proxy == args[0];
					default:
						break;
					}
				}
				Method same = find(h2, method);
				if (same != null) {
					try {
						return same.invoke(target, args);
					} catch (InvocationTargetException e) {
						throw e.getCause();
					}
				}
				Class<?> r = method.getReturnType();
				if (r == boolean.class) {
					return false;
				}
				if (r.isPrimitive() && r != void.class) {
					return r == long.class ? 0L : r == double.class ? 0d : r == float.class ? 0f : 0;
				}
				return null;
			});
		} catch (ReflectiveOperationException e) {
			throw new IllegalStateException("Base de pruebas no disponible para " + interfaceName + ": " + e, e);
		}
	}

	private static Method find(Class<?> type, Method m) {
		try {
			return type.getMethod(m.getName(), m.getParameterTypes());
		} catch (NoSuchMethodException e) {
			return null;
		}
	}
}
