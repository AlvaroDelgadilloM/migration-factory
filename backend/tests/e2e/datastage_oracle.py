"""DataStage -> Airflow end to end on a real Oracle (workflow datastage-e2e.yml): the project generated from
tests/fixtures/datastage/shop_flow.dsx runs in Airflow against Oracle and its reconciliation is compared with the legacy side.

There is no DataStage in CI: the legacy schema is filled by `legacy`, an independent row-by-row emulation of the fixture's
jobs (a Transformer processes rows in order, its stage variables keep the previous row; a comparison with null is false,
the parallel engine's null handling since 11.5). It cross-checks the set-based LAG() translation; equivalence with the real
DataStage still comes from running both on the client's environment.

    python datastage_oracle.py setup | ddl <staging.sql> | legacy | changed | tamper    (MF_E2E_ORACLE_DSN, MF_E2E_ORACLE_PASSWORD; synthetic data only)
"""
from __future__ import annotations

import datetime
import os
import sys

import oracledb

SCHEMAS = ('MF_LEGACY', 'MF_TARGET')
RUN_DATE = datetime.date(2026, 1, 1)   # default P_RUN_DATE of the generated DAG
ETL_USER = 'ETL_E2E'                   # Airflow Variable P_CONN__P_USER, also what the legacy side records
# (order_id, customer_id, amount, order_date): several orders per customer and one of another day. No null amounts: the
# staging DataSet column is not nullable in the DSX, so DataStage would reject them too.
ORDERS = [(101, 10, 50, RUN_DATE), (102, 10, 70, RUN_DATE), (103, 20, 30, RUN_DATE), (104, 30, 8, RUN_DATE),
          (105, 30, 15, RUN_DATE), (106, 20, 99, RUN_DATE - datetime.timedelta(days=1)), (107, 40, 12, RUN_DATE)]
TOTALS = [(101, 0), (102, 0), (103, 0), (105, 0), (106, 1), (200, 5)]   # 104/107 have no total row: the MERGE skips them


def connect(user='system'):
    pw = os.environ['MF_E2E_ORACLE_PASSWORD']
    return oracledb.connect(user=user, password=pw, dsn=os.environ.get('MF_E2E_ORACLE_DSN', 'localhost:1521/FREEPDB1'))


def setup():
    pw = os.environ['MF_E2E_ORACLE_PASSWORD']
    with connect() as c:
        cur = c.cursor()
        for s in SCHEMAS:
            cur.execute(f'CREATE USER {s} IDENTIFIED BY "{pw}" QUOTA UNLIMITED ON USERS')
            cur.execute(f'GRANT CREATE SESSION, CREATE TABLE TO {s}')
            cur.execute(f'CREATE TABLE {s}.ORDERS (ORDER_ID NUMBER PRIMARY KEY, CUSTOMER_ID NUMBER NOT NULL, AMOUNT NUMBER, ORDER_DATE DATE)')
            cur.execute(f'CREATE TABLE {s}.FIRST_ORDERS (CUSTOMER_ID NUMBER, AMOUNT NUMBER, LOADED_BY VARCHAR2(128), LOADED_AT TIMESTAMP)')
            cur.execute(f'CREATE TABLE {s}.ORDER_TOTALS (ORDER_ID NUMBER PRIMARY KEY, AMOUNT NUMBER)')
            cur.executemany(f'INSERT INTO {s}.ORDERS VALUES (:1, :2, :3, :4)', ORDERS)
            cur.executemany(f'INSERT INTO {s}.ORDER_TOTALS VALUES (:1, :2)', TOTALS)
        c.commit()
    print('esquemas', ', '.join(SCHEMAS))


def ddl(path):
    """sql/ddl/staging.sql of the generated project, in the schema the DAG connects to."""
    text = open(path, encoding='utf-8').read()
    body = '\n'.join(line for line in text.splitlines() if not line.startswith('--'))
    statements = [s.strip() for s in body.split(';') if s.strip()]
    with connect('MF_TARGET') as c:
        for st in statements:
            c.cursor().execute(st)
    print('staging', len(statements), 'tabla(s)')


def legacy():
    """What the DataStage jobs write, computed row by row (no SQL window functions)."""
    rows = sorted((o for o in ORDERS if o[3] == RUN_DATE), key=lambda o: (o[1], -o[0]))   # SO_200_SORT
    first, staged, customer = [], [], None
    for order_id, cust, amount, _ in rows:                     # TF_300_FLAG_FIRST
        prev = None if customer is None else customer          # PrevCustomer reads Customer of the previous row
        customer = cust
        flag = 'F' if prev is not None and customer != prev else 'N'
        if flag == 'F':
            first.append((cust, amount if amount is not None else 0, ETL_USER))
        staged.append((order_id, amount))                      # DS_500_ORDERS_STAGE (no constraint)
    with connect() as c:
        cur = c.cursor()
        cur.executemany("INSERT INTO MF_LEGACY.FIRST_ORDERS VALUES (:1, :2, :3, SYSTIMESTAMP)", first)
        # Oracle binds by position of appearance, not by number: the named binds keep AMOUNT and ORDER_ID in their place
        cur.executemany('UPDATE MF_LEGACY.ORDER_TOTALS SET AMOUNT = :amount WHERE ORDER_ID = :id',
                        [{'id': i, 'amount': a} for i, a in staged])                                   # JP_PUBLISH
        updated = cur.rowcount                                  # rows of the whole executemany
        c.commit()
    changed('MF_LEGACY')
    print('legado: FIRST_ORDERS', first, '· ORDER_TOTALS actualizadas', updated)


def changed(schema='MF_TARGET'):
    """The run wrote something: a job that ran no task (or wrote nothing) must not reconcile as equal to an idle legacy."""
    with connect() as c:
        cur = c.cursor()
        first = cur.execute(f'SELECT COUNT(*) FROM {schema}.FIRST_ORDERS').fetchone()[0]
        totals = dict(cur.execute(f'SELECT ORDER_ID, AMOUNT FROM {schema}.ORDER_TOTALS').fetchall())
    moved = {k for k, v in TOTALS if totals.get(k) != v}
    if not first or not moved:
        sys.exit(f'{schema}: el job no escribió (FIRST_ORDERS={first}, ORDER_TOTALS cambiadas={sorted(moved)})')
    print(f'{schema}: FIRST_ORDERS={first}, ORDER_TOTALS cambiadas={sorted(moved)}')


def tamper():
    """A wrong migrated row: the reconciliation must say DIFFERENT and name its key."""
    with connect() as c:
        c.cursor().execute('UPDATE MF_TARGET.ORDER_TOTALS SET AMOUNT = AMOUNT + 1 WHERE ORDER_ID = 103')
        c.commit()


if __name__ == '__main__':
    cmd = sys.argv[1]
    ddl(sys.argv[2]) if cmd == 'ddl' else {'setup': setup, 'legacy': legacy, 'changed': changed, 'tamper': tamper}[cmd]()
