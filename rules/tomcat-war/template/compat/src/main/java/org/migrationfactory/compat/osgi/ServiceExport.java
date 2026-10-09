package org.migrationfactory.compat.osgi;

import org.springframework.beans.factory.DisposableBean;
import org.springframework.beans.factory.InitializingBean;

/** Equivalente a &lt;service ref="..." interface="..."/&gt; de Blueprint. */
public class ServiceExport implements InitializingBean, DisposableBean {

	private Object ref;
	private String interfaceName;
	private String componentName;

	@Override
	public void afterPropertiesSet() {
		ServiceRegistry.register(interfaceName, componentName, ref);
	}

	@Override
	public void destroy() {
		ServiceRegistry.unregister(interfaceName, componentName, ref);
	}

	public void setRef(Object ref) {
		this.ref = ref;
	}

	public void setInterfaceName(String interfaceName) {
		this.interfaceName = interfaceName;
	}

	public void setComponentName(String componentName) {
		this.componentName = componentName;
	}
}
