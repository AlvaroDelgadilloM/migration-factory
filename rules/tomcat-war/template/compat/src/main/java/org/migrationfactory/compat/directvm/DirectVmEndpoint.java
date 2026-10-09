package org.migrationfactory.compat.directvm;

import org.apache.camel.Consumer;
import org.apache.camel.Processor;
import org.apache.camel.Producer;
import org.apache.camel.support.DefaultEndpoint;

public class DirectVmEndpoint extends DefaultEndpoint {

	private final String name;
	private boolean block = true;
	private long timeout = 30000L;
	private boolean failIfNoConsumers = true;
	private boolean propagateProperties = true;

	public DirectVmEndpoint(String uri, DirectVmComponent component, String name) {
		super(uri, component);
		this.name = name;
	}

	@Override
	public Producer createProducer() throws Exception {
		return new DirectVmProducer(this);
	}

	@Override
	public Consumer createConsumer(Processor processor) throws Exception {
		DirectVmConsumer consumer = new DirectVmConsumer(this, processor);
		configureConsumer(consumer);
		return consumer;
	}

	@Override
	public boolean isSingleton() {
		return true;
	}

	public String getName() {
		return name;
	}

	public boolean isBlock() {
		return block;
	}

	public void setBlock(boolean block) {
		this.block = block;
	}

	public long getTimeout() {
		return timeout;
	}

	public void setTimeout(long timeout) {
		this.timeout = timeout;
	}

	public boolean isFailIfNoConsumers() {
		return failIfNoConsumers;
	}

	public void setFailIfNoConsumers(boolean failIfNoConsumers) {
		this.failIfNoConsumers = failIfNoConsumers;
	}

	public boolean isPropagateProperties() {
		return propagateProperties;
	}

	public void setPropagateProperties(boolean propagateProperties) {
		this.propagateProperties = propagateProperties;
	}
}
