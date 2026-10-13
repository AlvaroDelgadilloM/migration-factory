package org.migrationfactory.compat.servlet;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import jakarta.servlet.ServletRequest;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;

/** Tomcat entrega los headers en minúsculas; las rutas los esperan como los manda el cliente. */
class HeaderCaseFilterTest {

	@Test
	void restoresTheNamesTheRoutesKnow() throws Exception {
		HttpServletRequest request = (HttpServletRequest) Proxy.newProxyInstance(getClass().getClassLoader(),
				new Class<?>[] { HttpServletRequest.class }, (proxy, method, args) -> "getHeaderNames".equals(method.getName())
						? Collections.enumeration(List.of("membernumber", "content-type", "x-desconocido"))
						: null);
		HeaderCaseFilter filter = new HeaderCaseFilter();
		filter.init(null);
		AtomicReference<ServletRequest> seen = new AtomicReference<>();
		filter.doFilter(request, null, (req, res) -> seen.set(req));
		List<String> names = new ArrayList<>(Collections.list(((HttpServletRequest) seen.get()).getHeaderNames()));
		// memberNumber viene de META-INF/mf/http-headers.txt; Content-Type es estándar; lo desconocido no se toca
		assertEquals(List.of("memberNumber", "Content-Type", "x-desconocido"), names);
	}
}
