"""Known data problems that stop an OpenUpgrade hop, found in real migrations (each one documented with the hop that fails).

Detected on the analysed database and shown with the affected records; a fix runs only on the migration COPY, only when an
architect approved it (or decided to go on without it), and is recorded. The original database is never changed."""
from __future__ import annotations

# id -> what fails, why, how it is detected (rows: id, name, detail) and the fix (SQL on the copy)
FIXES = {
    'company_partner_company': {
        'title': 'Contacto de una empresa asignado a otra empresa',
        'why': 'Odoo 15 (inventario) valida que el contacto de cada almacén sea de su empresa o compartido; si el contacto propio de '
               'una empresa pertenece a otra, el salto a 15.0 falla («Incompatible companies on records»).',
        'fix': 'El contacto de la empresa pasa a pertenecer a esa misma empresa.',
        'detect': 'select p.id, p.name, p.company_id::text || \' → \' || c.id::text from res_partner p '
                  'join res_company c on c.partner_id = p.id where p.company_id is not null and p.company_id <> c.id',
        'apply': 'update res_partner p set company_id = c.id from res_company c '
                 'where c.partner_id = p.id and p.company_id is not null and p.company_id <> c.id',
    },
    'warehouse_partner_company': {
        'title': 'Contacto de un almacén que pertenece a otra empresa',
        'why': 'Misma validación de Odoo 15: el contacto (dirección) de un almacén debe ser de la empresa del almacén o compartido.',
        'fix': 'El contacto pasa a ser compartido entre empresas (company_id vacío), sin cambiar a qué almacén pertenece.',
        'detect': 'select p.id, p.name, \'almacén \' || w.name from stock_warehouse w join res_partner p on p.id = w.partner_id '
                  'where p.company_id is not null and p.company_id <> w.company_id',
        'apply': 'update res_partner p set company_id = null from stock_warehouse w '
                 'where p.id = w.partner_id and p.company_id is not null and p.company_id <> w.company_id',
    },
}


def detect(query_all) -> list[dict]:
    """query_all(sql) -> list of rows, or None when a table does not exist. Only the problems present are returned."""
    out = []
    for key, f in FIXES.items():
        rows = query_all(f['detect'])
        if rows:
            out.append({'id': key, 'title': f['title'], 'why': f['why'], 'fix': f['fix'],
                        'records': [{'id': r[0], 'name': r[1], 'detail': r[2]} for r in rows[:50]], 'count': len(rows)})
    return out


def apply(execute, approved: list[str]) -> list[str]:
    """Run the approved fixes (execute(sql) on the copy). Returns the applied ids, in catalogue order."""
    done = []
    for key in FIXES:
        if key in approved:
            execute(FIXES[key]['apply'])
            done.append(key)
    return done



def before_hop(version: str, query_all, execute) -> list[str]:
    """Technical conversions between hops, for gaps of OpenUpgrade itself (no business meaning changes; logged and recorded).
    Before 19.0: the 18.0 migration stores company-dependent booleans in their jsonb column as numbers (1/0, from
    ir.property.value_integer) and Odoo 19 cannot cast them ("cannot cast jsonb numeric to type boolean")."""
    if version != '19.0':
        return []
    done = []
    for model, field in query_all("select m.model, f.name from ir_model_fields f join ir_model m on m.id = f.model_id "
                                  "where f.ttype = 'boolean' and f.company_dependent and f.store") or []:
        table = model.replace('.', '_')
        if not query_all(f"select 1 from information_schema.columns where table_name = '{table}' and column_name = '{field}' "
                         "and data_type = 'jsonb'"):
            continue
        numeric = f"""exists (select 1 from jsonb_each("{field}") e(k, v) where jsonb_typeof(v) = 'number')"""
        n = query_all(f'select count(*) from "{table}" where jsonb_typeof("{field}") = \'object\' and {numeric}')
        if n and n[0][0] not in ('0', 0):
            execute(f'''update "{table}" set "{field}" = (select jsonb_object_agg(k, case when jsonb_typeof(v) = 'number'
                       then to_jsonb((v::text)::numeric <> 0) else v end) from jsonb_each("{field}") e(k, v))
                       where jsonb_typeof("{field}") = 'object' and {numeric}''')
            done.append(f'{table}.{field}: {n[0][0]} registro(s) con booleanos guardados como número (1/0) por OpenUpgrade 18 → true/false')
    return done


if __name__ == '__main__':  # self-check: the order and the filtering
    seen = []
    assert apply(seen.append, ['warehouse_partner_company']) == ['warehouse_partner_company'] and 'stock_warehouse' in seen[0]
    found = detect(lambda sql: [(41, 'My Company, Chicago', '1 → 2')] if 'res_company c on' in sql else [])
    assert [f['id'] for f in found] == ['company_partner_company'] and found[0]['records'][0]['id'] == 41
    print('ok')
