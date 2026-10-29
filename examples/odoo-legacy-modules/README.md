# Módulos Odoo 12 a la medida (sintéticos)

Cinco módulos de Odoo 12 escritos a propósito con código legacy (`@api.multi`, `@api.one`, `track_visibility`, `attrs`, `<tree>`,
`account.invoice`, `stock.production.lot`, SQL directo, vista SQL `_auto = False`, tabla externa). Sin datos reales.

Prueba del flujo «Portar código» de un proyecto Odoo 12 → 19 (`mf.odoo.code`): inventario por archivo y línea, port automático
con OCA odoo-module-migrator e instalación de cada módulo portado en un Odoo 19 nuevo (`.github/workflows/odoo-e2e.yml`).
