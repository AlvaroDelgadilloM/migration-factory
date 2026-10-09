import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;

import org.apache.camel.component.servlet.CamelHttpTransportServlet;
import org.apache.cxf.Bus;
import org.apache.cxf.transport.servlet.CXFNonSpringServlet;
import org.eclipse.jetty.server.Server;
import org.eclipse.jetty.servlet.ServletContextHandler;
import org.eclipse.jetty.servlet.ServletHolder;
import org.springframework.context.ApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;
import org.springframework.context.support.FileSystemXmlApplicationContext;

/**
 * LINEA BASE: ejecuta el codigo ORIGINAL (sin migrar) sobre Camel 2.23.2, CXF 3.2 y Jetty, que es
 * la combinacion que hoy corre dentro de Karaf. Sirve para mandarle las mismas peticiones que al
 * WAR migrado y comparar las respuestas.
 *
 * No es Karaf: los beans se cablean con Spring en lugar de Blueprint (tools/baseline-xml.ps1
 * convierte los XML sin tocar su contenido). Las rutas, procesadores, XSLT, WSDL y la version de
 * Camel son los originales.
 *
 * Uso: java BaselineServer <puerto> <servlet=alias,...> <dirXmlModulo> [<dirXmlModulo> ...]
 */
public class BaselineServer {

	public static void main(String[] args) {
		try {
			run(args);
		} catch (Throwable e) {
			// sin esto la JVM queda viva (hilos de Spring/Jetty) ocupando el puerto aunque el arranque haya fallado
			e.printStackTrace();
			System.exit(1);
		}
	}

	private static void run(String[] args) throws Exception {
		int port = Integer.parseInt(args[0]);

		ClassPathXmlApplicationContext root = new ClassPathXmlApplicationContext("META-INF/cxf/cxf.xml");
		Bus bus = root.getBean("cxf", Bus.class);

		Server server = new Server(port);
		ServletContextHandler handler = new ServletContextHandler(ServletContextHandler.NO_SESSIONS);
		handler.setContextPath("/");
		CXFNonSpringServlet cxf = new CXFNonSpringServlet();
		cxf.setBus(bus);
		handler.addServlet(new ServletHolder("CXFServlet", cxf), "/cxf/*");
		for (String pair : args[1].split(",")) {
			String[] p = pair.split("=");
			ServletHolder h = new ServletHolder(p[0], new CamelHttpTransportServlet());
			h.setInitOrder(1);
			handler.addServlet(h, p[1] + "/*");
		}
		server.setHandler(handler);
		server.start();

		// primero los modulos que solo exportan servicios, igual que el cargador del WAR
		List<File> modules = new ArrayList<>();
		for (String dir : Arrays.copyOfRange(args, 2, args.length)) {
			modules.add(new File(dir));
		}
		modules.sort(Comparator.comparingInt(BaselineServer::order).thenComparing(File::getName));
		for (File m : modules) {
			long t0 = System.currentTimeMillis();
			File[] xml = m.listFiles((d, n) -> n.endsWith(".xml"));
			String[] locations = Arrays.stream(xml).map(f -> f.toURI().toString()).toArray(String[]::new);
			ApplicationContext ctx = new FileSystemXmlApplicationContext(locations, root);
			System.out.println("BASELINE modulo " + m.getName() + " iniciado en " + (System.currentTimeMillis() - t0)
					+ " ms (" + ctx.getBeanDefinitionCount() + " beans)");
		}
		System.out.println("BASELINE LISTO en puerto " + port);
		server.join();
	}

	private static int order(File module) {
		boolean exports = false;
		boolean imports = false;
		File[] xml = module.listFiles((d, n) -> n.endsWith(".xml"));
		if (xml != null) {
			for (File f : xml) {
				try {
					String text = new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8);
					exports |= text.contains("compat.osgi.ServiceExport");
					imports |= text.contains("compat.osgi.ServiceImport");
				} catch (Exception e) {
					// se ordena al final
				}
			}
		}
		return exports && !imports ? 0 : exports ? 1 : 2;
	}
}
