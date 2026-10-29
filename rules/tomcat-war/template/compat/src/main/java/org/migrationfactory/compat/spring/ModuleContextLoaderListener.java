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
			modules = findModules(container(sce.getServletContext()), list(sce.getServletContext(), "mf.waves"),
					list(sce.getServletContext(), "mf.consumers.off"));
		} catch (Exception e) {
			throw new IllegalStateException("No se pudieron localizar los modulos", e);
		}
		List<String> failed = new ArrayList<>();
		for (Module module : modules) {
			long t0 = System.currentTimeMillis();
			GenericApplicationContext ctx = new GenericApplicationContext(root);
			ctx.setId(module.name);
			ctx.setDisplayName("modulo " + module.name);
			ctx.getBeanFactory().addBeanPostProcessor(new LegacyCamelSettings(module.consumersOff));
			try {
				new XmlBeanDefinitionReader(ctx).loadBeanDefinitions(module.resources.toArray(new Resource[0]));
				ctx.refresh();
				started.push(ctx);
				LOG.info("Modulo {} iniciado en {} ms ({} archivos){}", module.name, System.currentTimeMillis() - t0,
						module.resources.size(), module.internal ? " en modo interno (lo usa otra ola activa; sin colas ni tareas)"
								: module.consumersOff ? " sin colas ni tareas (mf.consumers.off)" : "");
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

	/**
	 * Contenedor de origen a reproducir: -Dmf.container o, en un WAR generado para un solo contenedor, el context-param
	 * mf.container de su web.xml. Vacio = todos los modulos.
	 */
	static String container(jakarta.servlet.ServletContext servletContext) {
		String value = System.getProperty("mf.container", "").trim();
		if (value.isEmpty() && servletContext != null) {
			String param = servletContext.getInitParameter("mf.container");
			value = param == null ? "" : param.trim();
		}
		return value;
	}

	/** Lista separada por comas de -D&lt;name&gt; o del context-param &lt;name&gt;; vacia si no hay. */
	static java.util.Set<String> list(jakarta.servlet.ServletContext servletContext, String name) {
		String value = System.getProperty(name, "").trim();
		if (value.isEmpty() && servletContext != null) {
			String param = servletContext.getInitParameter(name);
			value = param == null ? "" : param.trim();
		}
		java.util.Set<String> out = new java.util.LinkedHashSet<>();
		for (String v : value.split(",")) {
			if (!v.trim().isEmpty()) {
				out.add(v.trim());
			}
		}
		return out;
	}

	private List<Module> findModules(String container, java.util.Set<String> waves, java.util.Set<String> consumersOff) throws Exception {
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
		Map<String, java.util.Properties> props = new TreeMap<>();
		for (Module m : byName.values()) {
			java.util.Properties p = new java.util.Properties();
			Resource file = m.resources.get(0).createRelative("module.properties");
			if (file.exists()) {
				try (java.io.InputStream in = file.getInputStream()) {
					p.load(in);
				}
			}
			props.put(m.name, p);
		}
		Map<String, String> chosen = select(props, container, waves, consumersOff);
		for (java.util.Iterator<Module> it = byName.values().iterator(); it.hasNext();) {
			Module m = it.next();
			String mode = chosen.get(m.name);
			if (mode == null) {
				LOG.info("Modulo {} omitido: {}", m.name, why(props.get(m.name), container, waves));
				it.remove();
			} else {
				m.internal = INTERNAL.equals(mode);
				m.consumersOff = !ACTIVE.equals(mode);
			}
		}
		List<Module> modules = new ArrayList<>(byName.values());
		modules.sort(Comparator.comparingInt(Module::order).thenComparing(m -> m.name));
		return modules;
	}

	static final String ACTIVE = "activo";
	static final String INTERNAL = "interno";
	static final String PASSIVE = "sin-consumidores";

	/**
	 * Que modulos arrancan y como, segun su module.properties (containers, wave, needs):
	 * 1) mobile|web1|web2: solo los que hoy van a ese contenedor de Karaf; sin contenedor, todos.
	 * 2) mf.waves: solo los de esas olas (y los que no tienen ola: librerias, servlets); sin olas, todos.
	 * 3) Lo que un modulo elegido llama en otro (needs: direct-vm, servicios OSGi) arranca aunque su ola no este activa, en modo
	 * interno: sin colas ni tareas programadas. Asi revertir una ola nunca rompe a otra activa.
	 * 4) mf.consumers.off: las olas cuyas colas y tareas siguen detenidas (durante el cambio desde el sistema actual).
	 * Devuelve nombre -&gt; ACTIVE | INTERNAL | PASSIVE; los que no arrancan no estan.
	 */
	static Map<String, String> select(Map<String, java.util.Properties> props, String container, java.util.Set<String> waves,
			java.util.Set<String> consumersOff) {
		Map<String, java.util.Properties> deployed = new TreeMap<>();
		props.forEach((name, p) -> {
			if (container.isEmpty() || values(p, "containers", container).contains(container)) {
				deployed.put(name, p);
			}
		});
		Map<String, String> out = new TreeMap<>();
		Deque<String> todo = new ArrayDeque<>();
		deployed.forEach((name, p) -> {
			String wave = p.getProperty("wave", "").trim();
			if (waves.isEmpty() || wave.isEmpty() || waves.contains(wave)) {
				out.put(name, !wave.isEmpty() && consumersOff.contains(wave) ? PASSIVE : ACTIVE);
				todo.add(name);
			}
		});
		while (!todo.isEmpty()) {
			for (String need : values(deployed.get(todo.pop()), "needs", "")) {
				if (deployed.containsKey(need) && !out.containsKey(need)) {
					out.put(need, INTERNAL);
					todo.add(need);
				}
			}
		}
		return out;
	}

	private static List<String> values(java.util.Properties p, String key, String fallback) {
		List<String> out = new ArrayList<>();
		for (String v : p.getProperty(key, fallback).split(",")) {
			if (!v.trim().isEmpty()) {
				out.add(v.trim());
			}
		}
		return out;
	}

	private static String why(java.util.Properties p, String container, java.util.Set<String> waves) {
		if (!container.isEmpty() && !values(p, "containers", container).contains(container)) {
			return "no se despliega en el contenedor '" + container + "' " + values(p, "containers", container);
		}
		return "su ola " + p.getProperty("wave", "") + " no esta activa " + waves;
	}

	private static final class Module {
		final String name;
		final List<Resource> resources = new ArrayList<>();
		boolean exports;
		boolean imports;
		boolean internal;
		boolean consumersOff;

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
