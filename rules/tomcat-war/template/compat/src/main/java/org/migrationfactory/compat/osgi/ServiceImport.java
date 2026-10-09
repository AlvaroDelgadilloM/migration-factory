package org.migrationfactory.compat.osgi;

import org.springframework.beans.factory.FactoryBean;

/** Equivalente a &lt;reference id="..." interface="..." component-name="..."/&gt; de Blueprint. */
public class ServiceImport implements FactoryBean<Object> {

	private String interfaceName;
	private String componentName;

	@Override
	public Object getObject() {
		return ServiceRegistry.lookup(interfaceName, componentName);
	}

	@Override
	public Class<?> getObjectType() {
		return null;
	}

	@Override
	public boolean isSingleton() {
		return true;
	}

	public void setInterfaceName(String interfaceName) {
		this.interfaceName = interfaceName;
	}

	public void setComponentName(String componentName) {
		this.componentName = componentName;
	}
}
