package org.migrationfactory.compat.xslt;

import java.util.Map;

import org.apache.camel.Endpoint;
import org.apache.camel.component.xslt.XsltComponent;
import org.apache.camel.component.xslt.saxon.XsltSaxonComponent;
import org.apache.camel.support.DefaultComponent;
import org.apache.camel.support.service.ServiceHelper;

/**
 * Componente "xslt" que acepta la opcion saxon=true de Camel 2.
 *
 * En Camel 4 Saxon se usa con el esquema xslt-saxon: y la opcion saxon ya no existe. Con este
 * componente las URIs actuales ("xslt:xsl/x.xsl?saxon=true") funcionan sin tocarlas y los
 * interceptores que filtran por "xslt:..." siguen coincidiendo.
 */
public class LegacyXsltComponent extends DefaultComponent {

	private final XsltSaxonComponent saxon = new XsltSaxonComponent();
	private final XsltComponent jdk = new XsltComponent();

	@Override
	protected Endpoint createEndpoint(String uri, String remaining, Map<String, Object> parameters) throws Exception {
		boolean useSaxon = getAndRemoveParameter(parameters, "saxon", boolean.class, false);
		// el componente delegado vuelve a leer los parametros de la URI: se le pasa sin "saxon"
		String cleanUri = uri.replaceAll("([?&])saxon=(true|false)(&|$)", "$1").replaceAll("[?&]$", "");
		Endpoint endpoint = (useSaxon ? saxon : jdk).createEndpoint(cleanUri, parameters);
		// los parametros ya los consumio el componente delegado
		parameters.clear();
		return endpoint;
	}

	@Override
	protected void doInit() throws Exception {
		super.doInit();
		saxon.setCamelContext(getCamelContext());
		jdk.setCamelContext(getCamelContext());
		ServiceHelper.initService(saxon, jdk);
	}

	@Override
	protected void doStart() throws Exception {
		super.doStart();
		ServiceHelper.startService(saxon, jdk);
	}

	@Override
	protected void doStop() throws Exception {
		ServiceHelper.stopService(saxon, jdk);
		super.doStop();
	}
}
