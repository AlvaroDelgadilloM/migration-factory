"""Target profile `tomcat-war`: Fuse/Karaf bundles (Camel 2 + OSGi Blueprint) -> one WAR for Tomcat (Camel 4 + Spring XML).

Unlike the Spring Boot / Quarkus profiles this one does not redesign the application: routes, processors, XSLT, WSDL and
SQL stay as they are and a small compatibility layer (rules/tomcat-war/template/compat) supplies what Camel 4 and Tomcat
no longer provide (direct-vm, xmljson, `xslt:...?saxon=true`, the OSGi service registry, one Spring context per module).
Stdlib only, like the scanner: `pipeline.migrate()` never runs project code and never writes into the source tree.
"""
