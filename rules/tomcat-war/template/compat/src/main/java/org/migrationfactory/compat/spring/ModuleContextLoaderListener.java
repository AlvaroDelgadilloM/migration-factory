package org.migrationfactory.compat.spring;

import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.xml.XmlBeanDefinitionReader;
import org.springframework.context.ApplicationContext;
import org.springframework.context.support.GenericApplicationContext;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.util.StreamUtils;
import org.springframework.web.context.support.WebApplicationContextUtils;

import jakarta.servlet.ServletContextEvent;
import jakarta.servlet.ServletContextListener;

/**
 * Carga cada modulo (antes un bundle OSGi) en su propio contexto Spring hijo, con su propio
 * CamelContext. Asi se conserva el aislamiento que daba Karaf: los ids de beans, los nombres
 * direct:, los routeId y los restConfiguration() pueden repetirse entre modulos sin renombrar.
 *
 * Cada modulo aporta sus XML en META-INF/modules/&lt;nombre&gt;/*.xml. Primero se cargan los que
 * solo exportan servicios, luego los que exportan e importan y al final los que solo importan.
 */
public class ModuleContextLoaderListener implements ServletContextListener {

	public static final String MODULES_PATTERN = "classpath*:META-INF/modules/*/*.xml";

	private static final Logger LOG = LoggerFactory.getLogger(ModuleContextLoaderListener.class);

	private final Deque<GenericApplicationContext> started = new ArrayDeque<>();

	@Override
	public void contextInitialized(ServletContextEvent sce) {
		ApplicationContext root = WebApplicationContextUtils.getRequiredWebApplicationContext(sce.getServletContext());
		// Como en Karaf, un modulo que falla no impide que arranquen los demas
		// (-Dmf.modules.failFast=true detiene todo al primer error).
		boolean failFast = Boolean.getBoolean("mf.modules.failFast");
		List<Module> modules;
		try {
			modules = findModules();
		} catch (Exception e) {
			throw new IllegalStateException("No se pudieron localizar los modulos", e);
		}
		List<String> failed = new ArrayList<>();
		for (Module module : modules) {
			long t0 = System.currentTimeMillis();
			GenericApplicationContext ctx = new GenericApplicationContext(root);
			ctx.setId(module.name);
			ctx.setDisplayName("modulo " + module.name);
			ctx.getBeanFactory().addBeanPostProcessor(new LegacyCamelSettings());
			try {
				new XmlBeanDefinitionReader(ctx).loadBeanDefinitions(module.resources.toArray(new Resource[0]));
				ctx.refresh();
				started.push(ctx);
				LOG.info("Modulo {} iniciado en {} ms ({} archivos)", module.name, System.currentTimeMillis() - t0,
						module.resources.size());
			} catch (Exception e) {
				Throwable cause = e;
				while (cause.getCause() != null && cause.getCause() != cause) {
					cause = cause.getCause();
				}
				failed.add(module.name);
				LOG.error("Modulo {} FALLO: {}: {}", module.name, cause.getClass().getSimpleName(), cause.getMessage());
				LOG.debug("Detalle del fallo de " + module.name, e);
				try {
					ctx.close();
				} catch (Exception ignored) {
					// el contexto quedo a medias
				}
				if (failFast) {
					contextDestroyed(sce);
					throw new IllegalStateException("No se pudo iniciar el modulo " + module.name, e);
				}
			}
		}
		LOG.info("Modulos iniciados: {} de {}. Con falla: {}", started.size(), modules.size(), failed);
	}

	@Override
	public void contextDestroyed(ServletContextEvent sce) {
		while (!started.isEmpty()) {
			GenericApplicationContext ctx = started.pop();
			try {
				ctx.close();
			} catch (Exception e) {
				LOG.warn("Error al detener el modulo {}", ctx.getId(), e);
			}
		}
	}

	private List<Module> findModules() throws Exception {
		Map<String, Module> byName = new TreeMap<>();
		for (Resource r : new PathMatchingResourcePatternResolver().getResources(MODULES_PATTERN)) {
			String[] parts = r.getURL().toString().split("/");
			String name = parts[parts.length - 2];
			Module m = byName.computeIfAbsent(name, Module::new);
			m.resources.add(r);
			String xml = StreamUtils.copyToString(r.getInputStream(), StandardCharsets.UTF_8);
			m.exports |= xml.contains("compat.osgi.ServiceExport");
			m.imports |= xml.contains("compat.osgi.ServiceImport");
		}
		// -Dmf.container=mobile|web1|web2: solo los modulos que hoy van a ese contenedor de
		// Karaf (module.properties). Sin la propiedad se cargan todos.
		String container = System.getProperty("mf.container", "").trim();
		if (!container.isEmpty()) {
			for (java.util.Iterator<Module> it = byName.values().iterator(); it.hasNext();) {
				Module m = it.next();
				Resource props = m.resources.get(0).createRelative("module.properties");
				if (props.exists()) {
					java.util.Properties p = new java.util.Properties();
					try (java.io.InputStream in = props.getInputStream()) {
						p.load(in);
					}
					List<String> allowed = java.util.Arrays.asList(p.getProperty("containers", container).split("\\s*,\\s*"));
					if (!allowed.contains(container)) {
						LOG.info("Modulo {} omitido: no se despliega en el contenedor '{}' {}", m.name, container, allowed);
						it.remove();
					}
				}
			}
		}
		List<Module> modules = new ArrayList<>(byName.values());
		modules.sort(Comparator.comparingInt(Module::order).thenComparing(m -> m.name));
		return modules;
	}

	private static final class Module {
		final String name;
		final List<Resource> resources = new ArrayList<>();
		boolean exports;
		boolean imports;

		Module(String name) {
			this.name = name;
		}

		int order() {
			if (exports && !imports) {
				return 0;
			}
			return exports ? 1 : 2;
		}
	}
}
