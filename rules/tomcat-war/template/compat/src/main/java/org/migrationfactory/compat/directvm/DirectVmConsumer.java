package org.migrationfactory.compat.directvm;

import org.apache.camel.Processor;
import org.apache.camel.support.DefaultConsumer;

public class DirectVmConsumer extends DefaultConsumer {

	public DirectVmConsumer(DirectVmEndpoint endpoint, Processor processor) {
		super(endpoint, processor);
	}

	@Override
	public DirectVmEndpoint getEndpoint() {
		return (DirectVmEndpoint) super.getEndpoint();
	}

	@Override
	protected void doStart() throws Exception {
		super.doStart();
		DirectVmComponent.addConsumer(getEndpoint().getName(), this);
	}

	@Override
	protected void doStop() throws Exception {
		DirectVmComponent.removeConsumer(getEndpoint().getName(), this);
		super.doStop();
	}
}
