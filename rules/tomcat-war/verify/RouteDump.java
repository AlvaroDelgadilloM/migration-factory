import java.io.File;
import java.io.PrintWriter;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.net.URL;
import java.net.URLClassLoader;
import java.net.URLDecoder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.TreeMap;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * Vuelca la ESTRUCTURA de las rutas que define cada RouteBuilder de un modulo, sin arrancar nada:
 * solo ejecuta configure() y recorre el modelo. Usa reflexion para que el mismo programa sirva
 * con Camel 2.23 (fuentes originales) y con Camel 4 (fuentes migrados); asi los dos volcados
 * tienen el mismo formato y se pueden comparar linea por linea.
 *
 * Uso: java RouteDump <dirClasesDelModulo> <archivoSalida>
 * (el classpath debe traer Camel y las dependencias del modulo)
 */
public class RouteDump {

	// getters que no describen comportamiento o que cambian de forma entre versiones
	private static final List<String> SKIP = List.of("getId", "getCustomId", "getParent", "getParentId", "getOutputs",
			"getClass", "getLabel", "getShortName", "getDescription", "getDescriptionText", "getLineNumber",
			"getLocation", "getOtherAttributes", "getIndex", "getInheritErrorHandler", "getWhenClauses",
			"getOtherwise", "getExpression", "getInterceptStrategies", "getEndpointUri", "getUriOrRef",
			"getRouteProperties", "getNodePrefixId", "getTargetDescription", "getRestDefinition",
			"getRestBindingDefinition", "getCamelContext", "getErrorHandlerFactory", "getErrorHandlerBuilder",
			"getErrorHandlerRef", "getInput", "getInputs", "getRoutePolicies", "getGroup", "getRouteId",
			"getPrecondition", "getResource", "getTemplateParameters", "getAppliedRouteConfigurationIds",
			"getRouteConfigurationId", "getInputType", "getOutputType", "getRouteTemplateContext",
			"getStartupOrder", "getDisabled", "getExceptionClasses", "getExceptions", "getEndpoint", "getBean",
			"getProcessor", "getAggregationStrategy", "getAggregationStrategyRef", "getStrategyRef",
			"getUriDelimiter", "getExecutorService", "getExecutorServiceRef", "getOnPrepare", "getOnPrepareRef",
			"getDataFormatType", "getCustomLoadBalancer", "getLogger", "getLoggerRef", "getMarker", "getLogName",
			"getHeaderName", "getPropertyName", "getName", "getRef", "getTypeClass", "getBeanClass",
			"getRouteScoped", "getErrorHandler", "getRetryWhile", "getHandled", "getContinued",
			"getRedeliveryPolicy", "getRedeliveryPolicyType", "getOnRedelivery", "getOnExceptionOccurred",
			"getOnWhen", "getVariableSend", "getVariableReceive", "getDisableVariable", "getAllowNullBody",
			"getUseOriginalMessage", "getUseOriginalMessagePolicy", "getUseOriginalBody",
			"getUseOriginalBodyPolicy", "getStrategyMethodAllowNull", "getAggregationStrategyMethodAllowNull",
			"getStrategyMethodName", "getAggregationStrategyMethodName");

	private static boolean camel2;

	public static void main(String[] args) throws Exception {
		File classesDir = new File(args[0]);
		ClassLoader cl = new URLClassLoader(new URL[] { classesDir.toURI().toURL() }, RouteDump.class.getClassLoader());
		Thread.currentThread().setContextClassLoader(cl);
		Class<?> builderType = cl.loadClass("org.apache.camel.builder.RouteBuilder");
		Class<?> routesBuilder = cl.loadClass("org.apache.camel.RoutesBuilder");
		Class<?> contextType = cl.loadClass("org.apache.camel.CamelContext");
		Class<?> contextImpl = cl.loadClass("org.apache.camel.impl.DefaultCamelContext");
		camel2 = !hasMethod(contextImpl, "getCamelContextExtension");

		List<String> classNames;
		try (Stream<Path> s = Files.walk(classesDir.toPath())) {
			classNames = s.filter(p -> p.toString().endsWith(".class") && !p.getFileName().toString().contains("$"))
					.map(p -> classesDir.toPath().relativize(p).toString().replace(File.separatorChar, '.')
							.replaceAll("\\.class$", ""))
					.sorted().collect(Collectors.toList());
		}

		Map<String, List<String>> routes = new TreeMap<>();
		List<String> errors = new ArrayList<>();
		for (String name : classNames) {
			Class<?> c;
			try {
				c = cl.loadClass(name);
			} catch (Throwable t) {
				continue;
			}
			if (!builderType.isAssignableFrom(c) || Modifier.isAbstract(c.getModifiers())) {
				continue;
			}
			try {
				Object ctx = contextImpl.getConstructor().newInstance();
				if (camel2) {
					// Camel 2 resuelve {{clave}} al definir la ruta; Camel 4 lo deja para el arranque.
					// Se le da un valor @clave@ para cualquier clave y el comparador unifica ambos.
					Object pc = cl.loadClass("org.apache.camel.component.properties.PropertiesComponent")
							.getConstructor().newInstance();
					Class<?> parserType = cl.loadClass("org.apache.camel.component.properties.PropertiesParser");
					Object parser = java.lang.reflect.Proxy.newProxyInstance(cl, new Class<?>[] { parserType },
							(proxy, method, a) -> {
								if ("parseUri".equals(method.getName())) {
									return String.valueOf(a[0]).replaceAll("\\{\\{([\\w.\\-]+)\\}\\}", "@$1@");
								}
								if ("parseProperty".equals(method.getName())) {
									return a[1];
								}
								return method.getReturnType() == boolean.class ? Boolean.FALSE : null;
							});
					pc.getClass().getMethod("setPropertiesParser", parserType).invoke(pc, parser);
					contextImpl.getMethod("addComponent", String.class, cl.loadClass("org.apache.camel.Component"))
							.invoke(ctx, "properties", pc);
				} else {
					// igual que LegacyCamelSettings en la version migrada
					Object rc = call(ctx, "getRestConfiguration");
					rc.getClass().getMethod("setInlineRoutes", boolean.class).invoke(rc, false);
				}
				Object rb = c.getConstructor().newInstance();
				prepare(rb, ctx, contextType, builderType);
				contextImpl.getMethod("addRoutes", routesBuilder).invoke(ctx, rb);
				for (Object route : (Collection<?>) call(ctx, "getRouteDefinitions")) {
					List<String> lines = new ArrayList<>();
					for (Object out : outputs(route)) {
						dump(out, "  ", lines);
					}
					String key = c.getSimpleName() + " | " + fromUri(route);
					while (routes.containsKey(key)) {
						key += " (bis)";
					}
					routes.put(key, lines);
				}
			} catch (Throwable t) {
				Throwable cause = t;
				while (cause.getCause() != null) {
					cause = cause.getCause();
				}
				errors.add(c.getSimpleName() + " : " + cause.getClass().getSimpleName() + ": " + cause.getMessage());
			}
		}

		try (PrintWriter out = new PrintWriter(args[1], "UTF-8")) {
			for (Map.Entry<String, List<String>> e : routes.entrySet()) {
				out.println("ROUTE " + e.getKey());
				e.getValue().forEach(out::println);
			}
			for (String err : errors) {
				out.println("ERROR " + err);
			}
		}
		System.out.println(classesDir.getParentFile().getName() + ": " + routes.size() + " rutas, " + errors.size() + " errores");
	}

	/**
	 * Los RouteBuilder se crean aqui sin Spring/Blueprint: se imita lo minimo que hacia el contenedor.
	 * Los colaboradores propios de la aplicacion que quedarian en null se crean vacios, y se llama
	 * a init() si existe (init-method del bean).
	 */
	private static void prepare(Object rb, Object ctx, Class<?> contextType, Class<?> builderType) {
		for (Class<?> k = rb.getClass(); k != null && isApplicationType(k); k = k.getSuperclass()) {
			for (Field f : k.getDeclaredFields()) {
				try {
					if (Modifier.isStatic(f.getModifiers()) || !isApplicationType(f.getType())) {
						continue;
					}
					f.setAccessible(true);
					if (f.get(rb) == null) {
						Object stub = f.getType().getConstructor().newInstance();
						if (builderType.isInstance(stub)) {
							for (String setter : new String[] { "setCamelContext", "setContext" }) {
								try {
									stub.getClass().getMethod(setter, contextType).invoke(stub, ctx);
								} catch (NoSuchMethodException ignored) {
									// la otra version
								}
							}
						}
						f.set(rb, stub);
					}
				} catch (Throwable ignored) {
					// se queda en null
				}
			}
		}
		try {
			rb.getClass().getMethod("init").invoke(rb);
		} catch (Throwable ignored) {
			// sin init()
		}
	}

	/** Clases de la aplicacion: ni del JDK ni de las librerias de integracion. */
	private static boolean isApplicationType(Class<?> type) {
		if (type.isPrimitive() || type.isArray() || type.isInterface() || type.isEnum()) {
			return false;
		}
		String n = type.getName();
		for (String prefix : new String[] { "java.", "javax.", "jakarta.", "org.apache.", "org.springframework.", "org.slf4j.", "com.sun.",
				"org.w3c.", "org.xml.", "com.fasterxml.", "org.migrationfactory." }) {
			if (n.startsWith(prefix)) {
				return false;
			}
		}
		return true;
	}

	private static String fromUri(Object route) throws Exception {
		Object input;
		if (hasMethod(route.getClass(), "getInput")) {
			input = call(route, "getInput");
		} else {
			List<?> inputs = (List<?>) call(route, "getInputs");
			input = inputs.isEmpty() ? null : inputs.get(0);
		}
		if (input == null) {
			return "?";
		}
		Object uri = hasMethod(input.getClass(), "getEndpointUri") ? call(input, "getEndpointUri") : call(input, "getUri");
		return uri(String.valueOf(uri));
	}

	/** Las URIs rest: se escriben distinto en cada version; se dejan como rest:verbo:ruta. */
	private static String uri(String uri) {
		if (uri.startsWith("rest:") || uri.startsWith("rest-api:")) {
			int q = uri.indexOf('?');
			String base = q < 0 ? uri : uri.substring(0, q);
			try {
				base = URLDecoder.decode(base, "UTF-8");
			} catch (Exception e) {
				// se deja tal cual
			}
			return base.replace("rest://", "rest:").replaceAll("rest-api:/+", "rest-api:/");
		}
		return uri;
	}

	private static List<?> outputs(Object def) {
		try {
			Object o = call(def, "getOutputs");
			return o instanceof List ? (List<?>) o : Collections.emptyList();
		} catch (Exception e) {
			return Collections.emptyList();
		}
	}

	private static void dump(Object def, String indent, List<String> lines) throws Exception {
		String name = hasMethod(def.getClass(), "getShortName") ? String.valueOf(call(def, "getShortName"))
				: def.getClass().getSimpleName().replace("Definition", "").toLowerCase();
		StringBuilder sb = new StringBuilder(indent).append(name);
		String expr = expression(def);
		if (expr != null) {
			sb.append(" {").append(expr).append('}');
		}
		Map<String, String> attrs = new TreeMap<>();
		for (Method m : def.getClass().getMethods()) {
			if (m.getParameterCount() != 0 || !m.getName().startsWith("get") || SKIP.contains(m.getName())) {
				continue;
			}
			Class<?> rt = m.getReturnType();
			boolean simple = rt == String.class || Number.class.isAssignableFrom(rt) || rt == Boolean.class
					|| rt.isEnum() || rt == Class.class;
			boolean stringList = List.class.isAssignableFrom(rt);
			if (!simple && !stringList) {
				continue;
			}
			Object v;
			try {
				v = m.invoke(def);
			} catch (Exception e) {
				continue;
			}
			v = text(v);
			if (v != null) {
				String attr = Character.toLowerCase(m.getName().charAt(3)) + m.getName().substring(4);
				attrs.put(attr, "uri".equals(attr) ? uri(v.toString()) : v.toString());
			}
		}
		// atributos que cambiaron de nombre entre versiones: se reportan con un nombre comun
		put(attrs, "name", first(def, "getName", "getHeaderName", "getPropertyName"));
		put(attrs, "ref", first(def, "getRef"));
		put(attrs, "exceptions", first(def, "getExceptions", "getExceptionClasses"));
		put(attrs, "strategy", first(def, "getAggregationStrategy", "getAggregationStrategyRef", "getStrategyRef"));
		put(attrs, "useOriginalMessage", first(def, "getUseOriginalMessage", "getUseOriginalMessagePolicy"));
		put(attrs, "strategyMethodAllowNull",
				first(def, "getAggregationStrategyMethodAllowNull", "getStrategyMethodAllowNull"));
		put(attrs, "strategyMethodName", first(def, "getAggregationStrategyMethodName", "getStrategyMethodName"));
		if (hasMethod(def.getClass(), "getDataFormatType")) {
			Object df = call(def, "getDataFormatType");
			if (df != null) {
				Object ref = first(df, "getRef");
				put(attrs, "ref", ref != null ? ref : first(df, "getDataFormatName"));
			}
		}
		for (Map.Entry<String, String> a : attrs.entrySet()) {
			sb.append(' ').append(a.getKey()).append('=').append(a.getValue().replaceAll("\\s+", " "));
		}
		lines.add(sb.toString());

		if (hasMethod(def.getClass(), "getWhenClauses")) {
			for (Object w : (List<?>) call(def, "getWhenClauses")) {
				dump(w, indent + "  ", lines);
			}
			Object otherwise = call(def, "getOtherwise");
			if (otherwise != null) {
				dump(otherwise, indent + "  ", lines);
			}
		} else {
			for (Object out : outputs(def)) {
				dump(out, indent + "  ", lines);
			}
		}
	}

	private static void put(Map<String, String> attrs, String key, Object value) {
		if (value != null) {
			attrs.put(key, value.toString());
		}
	}

	/** Primer getter que exista y devuelva un valor simple. */
	private static Object first(Object target, String... getters) {
		for (String g : getters) {
			try {
				if (hasMethod(target.getClass(), g)) {
					Object v = text(call(target, g));
					if (v != null) {
						return v;
					}
				}
			} catch (Exception ignored) {
				// siguiente
			}
		}
		return null;
	}

	/** Valor como texto si es simple (o lista de simples); null en otro caso. */
	private static Object text(Object v) {
		if (v == null) {
			return null;
		}
		if (v instanceof Class) {
			return ((Class<?>) v).getName();
		}
		if (v instanceof List) {
			List<?> l = (List<?>) v;
			if (l.isEmpty() || !l.stream().allMatch(x -> x instanceof String || x instanceof Class)) {
				return null;
			}
			return l.stream().map(x -> x instanceof Class ? ((Class<?>) x).getName() : x.toString())
					.collect(Collectors.joining(","));
		}
		if (v instanceof String || v instanceof Number || v instanceof Boolean || v instanceof Enum) {
			String s = v.toString();
			return s.isEmpty() ? null : s;
		}
		return null;
	}

	private static String expression(Object def) {
		try {
			if (!hasMethod(def.getClass(), "getExpression")) {
				return null;
			}
			return expressionText(call(def, "getExpression"), 0);
		} catch (Exception ex) {
			return null;
		}
	}

	private static String expressionText(Object e, int depth) throws Exception {
		if (e == null || depth > 4) {
			return null;
		}
		if (e instanceof String) {
			return ((String) e).replaceAll("\\s+", " ");
		}
		// setBody().simple("..."): la expresion real esta dentro del ExpressionClause
		if (hasMethod(e.getClass(), "getExpressionType")) {
			Object inner = call(e, "getExpressionType");
			if (inner != null && inner != e) {
				return expressionText(inner, depth + 1);
			}
		}
		// llamada a bean: cada version la imprime distinto; se deja como bean:ref?method=m
		if (hasMethod(e.getClass(), "getMethod") && hasMethod(e.getClass(), "getRef")) {
			Object ref = first(e, "getRef", "getBeanTypeName");
			if (ref != null) {
				Object method = first(e, "getMethod");
				return "bean:" + ref + (method == null ? "" : "?method=" + method);
			}
		}
		Object text = hasMethod(e.getClass(), "getExpression") ? call(e, "getExpression") : null;
		Object lang = hasMethod(e.getClass(), "getLanguage") ? call(e, "getLanguage") : null;
		if (text instanceof String && !((String) text).isEmpty()) {
			String l = lang == null || lang.toString().isEmpty() ? "" : lang + ":";
			return l + ((String) text).replaceAll("\\s+", " ");
		}
		for (String getter : new String[] { "getPredicate", "getExpressionValue" }) {
			if (hasMethod(e.getClass(), getter)) {
				Object v = call(e, getter);
				if (v != null && v != e) {
					boolean wrapper = hasMethod(v.getClass(), "getExpressionType")
							|| hasMethod(v.getClass(), "getExpressionValue");
					String inner = wrapper ? expressionText(v, depth + 1) : null;
					return inner != null ? inner : v.toString().replaceAll("\\s+", " ");
				}
			}
		}
		String s = e.toString().replaceAll("\\s+", " ");
		// sin representacion estable (Objeto@hash): no aporta a la comparacion
		return s.matches(".*@[0-9a-f]{5,}$") ? e.getClass().getSimpleName() : s;
	}

	private static boolean hasMethod(Class<?> type, String name) {
		for (Method m : type.getMethods()) {
			if (m.getName().equals(name) && m.getParameterCount() == 0) {
				return true;
			}
		}
		return false;
	}

	private static Object call(Object target, String method) throws Exception {
		Method m = target.getClass().getMethod(method);
		m.setAccessible(true);
		return m.invoke(target);
	}
}
