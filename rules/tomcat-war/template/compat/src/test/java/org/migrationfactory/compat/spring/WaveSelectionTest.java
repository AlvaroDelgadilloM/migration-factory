package org.migrationfactory.compat.spring;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.TreeMap;

import org.apache.camel.ServiceStatus;
import org.apache.camel.builder.RouteBuilder;
import org.apache.camel.impl.DefaultCamelContext;
import org.junit.jupiter.api.Test;

/** Despliegue por olas: que arranca con mf.waves y mf.consumers.off, y que las colas y tareas quedan detenidas. */
class WaveSelectionTest {

	private static Properties props(String... kv) {
		Properties p = new Properties();
		for (int i = 0; i < kv.length; i += 2) {
			p.setProperty(kv[i], kv[i + 1]);
		}
		return p;
	}

	private static Map<String, Properties> workspace() {
		Map<String, Properties> m = new TreeMap<>();
		m.put("common-util", props("wave", "0"));
		m.put("member-security", props("wave", "1", "needs", "common-util"));
		m.put("booking", props("wave", "2", "needs", "member-security,ancillary-ws", "containers", "mobile"));
		m.put("ancillary-ws", props("wave", "4", "needs", "common-util"));
		m.put("servlets", props());
		return m;
	}

	@Test
	void withoutWavesEverythingStartsAsToday() {
		Map<String, String> all = ModuleContextLoaderListener.select(workspace(), "", Set.of(), Set.of());
		assertEquals(5, all.size());
		assertTrue(all.values().stream().allMatch(ModuleContextLoaderListener.ACTIVE::equals));
	}

	@Test
	void aWaveStartsWithWhatItCallsInInternalMode() {
		Map<String, String> s = ModuleContextLoaderListener.select(workspace(), "", Set.of("0", "2"), Set.of());
		assertEquals(ModuleContextLoaderListener.ACTIVE, s.get("booking"));
		assertEquals(ModuleContextLoaderListener.ACTIVE, s.get("common-util"));
		assertEquals(ModuleContextLoaderListener.ACTIVE, s.get("servlets"));
		// ola 1 y ola 4 no estan activas, pero la ola 2 las llama: arrancan sin colas ni tareas
		assertEquals(ModuleContextLoaderListener.INTERNAL, s.get("member-security"));
		assertEquals(ModuleContextLoaderListener.INTERNAL, s.get("ancillary-ws"));
	}

	@Test
	void rollingBackAWaveLeavesTheOthersUntouched() {
		Map<String, String> s = ModuleContextLoaderListener.select(workspace(), "", Set.of("0", "1"), Set.of());
		assertFalse(s.containsKey("booking"));
		assertFalse(s.containsKey("ancillary-ws"));
		assertEquals(ModuleContextLoaderListener.ACTIVE, s.get("member-security"));
	}

	@Test
	void containerAndConsumersOff() {
		Map<String, String> s = ModuleContextLoaderListener.select(workspace(), "web1", Set.of("0", "1", "2"), Set.of("1"));
		assertFalse(s.containsKey("booking"), "booking solo va en mobile");
		assertEquals(ModuleContextLoaderListener.PASSIVE, s.get("member-security"));
	}

	@Test
	void passiveModuleDoesNotStartQueuesOrTimers() throws Exception {
		try (DefaultCamelContext context = new DefaultCamelContext()) {
			context.addModelLifecycleStrategy(new LegacyCamelSettings.PassiveConsumers(context));
			context.getPropertiesComponent().addInitialProperty("job.uri", "timer:fromProperty?period=60000");
			context.addRoutes(new RouteBuilder() {
				@Override
				public void configure() {
					from("timer:job?period=60000").routeId("job").log("tick");
					from("{{job.uri}}").routeId("jobProp").log("tick");
					from("direct:answer").routeId("answer").setBody(constant("ok"));
				}
			});
			context.start();
			assertEquals(ServiceStatus.Stopped, context.getRouteController().getRouteStatus("job"));
			assertEquals(ServiceStatus.Stopped, context.getRouteController().getRouteStatus("jobProp"));
			assertEquals(ServiceStatus.Started, context.getRouteController().getRouteStatus("answer"));
			assertEquals("ok", context.createProducerTemplate().requestBody("direct:answer", "", String.class));
		}
	}
}
