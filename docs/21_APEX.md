# Oracle APEX entre versiones

Migrar aplicaciones Oracle APEX de una versión a otra, por saltos, pudiendo ver y descargar cada versión intermedia (a diferencia
de Odoo, donde solo se entrega el resultado final).

| Fase | Qué hace | Estado |
|---|---|---|
| 1. Análisis | Proyecto «Oracle APEX»: inventario por aplicación, plug-ins, cambios por versión y plan de saltos. Sin base de datos. | Hecha |
| 2. Saltos reales | Oracle con cada versión de APEX: importar, actualizar, exportar y comparar. Cada salto deja su export y sus diferencias. | Pendiente: instaladores de APEX y servidor |
| 3. Ver en vivo y pruebas | Levantar la versión elegida con ORDS y comparar páginas. | Pendiente: esquemas de las aplicaciones |

## Fase 1

**Entrada:** un ZIP con los exports `.sql` de las aplicaciones. Puede traer también el DDL de los esquemas. Al subirlo, la plataforma
lo reconoce por la cabecera `import_begin` de cada `.sql` y crea un proyecto tipo `APEX`.

**Análisis (`mf.apex.analysis`, trabajo `apex_analyze`):** lee cada export como texto, línea por línea, sin ejecutarlo. Salta los
archivos binarios (`varchar2_table`), que son la mayor parte del export. De cada aplicación saca:

- cabecera, estadísticas, modo de compatibilidad, tema y autenticación;
- plug-ins instalados y cuántas veces se usan;
- cambios que la afectan, con cuántos usos y en qué páginas o plug-ins;
- objetos que usa de los esquemas de las aplicaciones del ZIP y cuántos tienen DDL en el ZIP. Un volcado de otro motor (MariaDB) no cuenta.

**Qué no guarda:** nombres de usuario, correos ni contenido binario. Los exports reales no van al repositorio;
`examples/apex-demo` es sintético.

**Reglas (`rules/apex/catalog.json`):** cada una trae su versión (`since`) y la nota de Oracle de donde sale. `status`:

| Valor | Significado |
|---|---|
| `DESUPPORTED` | Retirado |
| `DEPRECATED` | Deprecado |
| `LEGACY` | Oracle lo marca Legacy |
| `UNCONFIRMED` | Sin aviso oficial encontrado |
| `INTERNAL` | No es API pública |

`severity`: `BLOCK` (deja de funcionar), `REVIEW` (revisar), `INFO`. Las reglas `UNCONFIRMED` e `INTERNAL` no siempre tienen fuente, y lo dicen.

**Plan de saltos:** `hops()` da la ruta, una versión por año o todas. Cada cambio pertenece al primer salto que alcanza su versión.
Lo que ya rige en el origen, o no depende de una versión, va antes del primer salto. Otro destino se recalcula sin volver a leer
los exports: `GET /apex-migrations/{id}/plan`.

**API:** `POST /projects/{id}/apex-analyses`, `GET /projects/{id}/apex-migrations`, `GET /apex-migrations/{id}/plan`, `GET /apex/versions`.

**Límite:** la fase 1 predice a partir de las notas de versión; no instala nada. Hasta la fase 2 no hay prueba de que una aplicación
importe y funcione en la versión de destino. APEX permite además importar directo en la última versión: los saltos sirven para ver
dónde cambia cada cosa y dejar puntos de regreso.
