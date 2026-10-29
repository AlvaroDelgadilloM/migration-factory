# Laboratorio complejo Odoo 12 (sintético)

Ocho módulos a la medida con dependencias entre sí (`custom_addons/`) y SQL externo (`backups/odoo12_complex_custom_backup.sql`
y los scripts de `database/init/`, con los mismos objetos y `CREATE DATABASE`/`\connect` de psql:
esquema `legacy_ext` con tablas, secuencia, funciones PL/pgSQL, trigger, vista y vista materializada, con datos semilla). No es
una base de Odoo: solo los objetos fuera del ORM. Sin datos reales.

Prueba del flujo «Portar código» con SQL externo (`.github/workflows/odoo-e2e.yml`, escenario `codecomplex`): port de los ocho
módulos, clasificación de cada objeto (KEEP, MIGRATE, REWRITE, MOVE_TO_ORM, DEPRECATE, MANUAL_REVIEW) y su ejecución en un Odoo 19
nuevo antes de instalar los módulos.
