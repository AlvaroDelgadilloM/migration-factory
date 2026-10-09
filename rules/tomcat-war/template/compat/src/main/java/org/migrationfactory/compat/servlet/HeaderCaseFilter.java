package org.migrationfactory.compat.servlet;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.FilterConfig;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;

/**
 * Tomcat entrega los nombres de los headers HTTP en minusculas (membernumber); el Jetty de
 * Karaf los entregaba tal como los manda el cliente (memberNumber). Las rutas dependen del
 * nombre exacto en dos lugares: los parametros de los XSLT (&lt;xsl:param name="memberNumber"/&gt;)
 * y el JSON/XML que se arma a partir de headers.
 *
 * Este filtro devuelve a cada header el nombre con el que las rutas lo conocen. La lista sale
 * de META-INF/mf/http-headers.txt, que tools/migrate.ps1 genera por modulo a partir de
 * los parametros REST de tipo header, de header("...") y de los xsl:param.
 */
public class HeaderCaseFilter implements Filter {

	public static final String NAMES_PATTERN = "classpath*:META-INF/mf/http-headers.txt";

	private static final Logger LOG = LoggerFactory.getLogger(HeaderCaseFilter.class);

	private static final String[] STANDARD = { "Accept", "Accept-Encoding", "Accept-Language", "Authorization",
			"Cache-Control", "Connection", "Content-Length", "Content-Type", "Cookie", "Host", "Origin", "Referer",
			"SOAPAction", "User-Agent" };

	private final Map<String, String> names = new HashMap<>();

	@Override
	public void init(FilterConfig config) throws ServletException {
		for (String n : STANDARD) {
			names.put(n.toLowerCase(Locale.ROOT), n);
		}
		try {
			for (Resource r : new PathMatchingResourcePatternResolver().getResources(NAMES_PATTERN)) {
				try (BufferedReader in = new BufferedReader(
						new InputStreamReader(r.getInputStream(), StandardCharsets.UTF_8))) {
					String line;
					while ((line = in.readLine()) != null) {
						line = line.trim();
						if (!line.isEmpty() && !line.startsWith("#")) {
							names.putIfAbsent(line.toLowerCase(Locale.ROOT), line);
						}
					}
				}
			}
		} catch (IOException e) {
			throw new ServletException("No se pudo leer " + NAMES_PATTERN, e);
		}
		LOG.info("HeaderCaseFilter: {} nombres de header conocidos", names.size());
	}

	@Override
	public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
			throws IOException, ServletException {
		if (request instanceof HttpServletRequest) {
			chain.doFilter(new CaseRestoringRequest((HttpServletRequest) request), response);
		} else {
			chain.doFilter(request, response);
		}
	}

	private final class CaseRestoringRequest extends HttpServletRequestWrapper {

		CaseRestoringRequest(HttpServletRequest request) {
			super(request);
		}

		// getHeader(name) ya es insensible a mayusculas; solo cambia el nombre que se reporta
		@Override
		public Enumeration<String> getHeaderNames() {
			List<String> restored = new ArrayList<>();
			Enumeration<String> original = super.getHeaderNames();
			while (original != null && original.hasMoreElements()) {
				String name = original.nextElement();
				restored.add(names.getOrDefault(name.toLowerCase(Locale.ROOT), name));
			}
			return Collections.enumeration(restored);
		}
	}
}
