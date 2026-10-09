# Fixture sintética: bundles Fuse/Karaf (Camel 2 + Blueprint)

Tres bundles OSGi mínimos para probar el perfil `tomcat-war` (docs/19_TOMCAT_WAR.md). No provienen de ningún cliente.

- `common-services`: exporta un servicio OSGi y una ruta `direct-vm`.
- `orders-api`: importa el servicio, usa Rest DSL sobre servlet, `xmljson`, `xslt ...?saxon=true` y `direct-vm`.
- `web-servlets`: registra el servlet REST con `OsgiServletRegisterer`.

`orders-api` importa además `auditStore`, que ningún bundle exporta: reproduce el caso real de servicios que solo existen
en lo desplegado.
