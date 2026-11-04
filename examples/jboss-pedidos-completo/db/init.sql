-- Base sintética del ejemplo «pedidos completo» (sin datos reales). Cada caso de prueba usa un registro preparado aquí.
CREATE TABLE clientes (
    id              INTEGER PRIMARY KEY,
    nombre          VARCHAR(120) NOT NULL,
    email           VARCHAR(160) NOT NULL UNIQUE,
    limite_credito  NUMERIC(12,2) NOT NULL,
    activo          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE productos (
    id        INTEGER PRIMARY KEY,
    nombre    VARCHAR(120) NOT NULL,
    categoria VARCHAR(40)  NOT NULL,
    precio    NUMERIC(12,2) NOT NULL
);

CREATE TABLE inventario (
    producto_id INTEGER PRIMARY KEY REFERENCES productos(id),
    existencias INTEGER NOT NULL CHECK (existencias >= 0)
);

CREATE TABLE pedidos (
    id          SERIAL PRIMARY KEY,
    cliente_id  INTEGER NOT NULL REFERENCES clientes(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad    INTEGER NOT NULL CHECK (cantidad BETWEEN 1 AND 100),
    total       NUMERIC(10,2) NOT NULL,          -- un total que no cabe aquí provoca el rollback del caso «pedido-rollback»
    estado      VARCHAR(20) NOT NULL,
    folio       VARCHAR(40),
    creado      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE pedido_eventos (
    id        SERIAL PRIMARY KEY,
    pedido_id INTEGER,
    evento    VARCHAR(30) NOT NULL,
    detalle   VARCHAR(400),
    creado    TIMESTAMP NOT NULL DEFAULT now()
);

INSERT INTO clientes VALUES
 (1, 'Comercial Andina',    'compras@andina.example',   50000, TRUE),
 (2, 'Distribuidora Lago',  'pedidos@lago.example',     20000, TRUE),
 (3, 'Tienda Cerrada',      'baja@cerrada.example',     10000, FALSE),  -- inactivo: 422
 (4, 'Riesgo Alto',         'riesgo@alto.example',      10000, TRUE),   -- el buró rechaza su crédito: 422
 (5, 'Buró Lento',          'lento@buro.example',       10000, TRUE);   -- el buró no responde a tiempo: 503

INSERT INTO productos VALUES
 (1, 'Café de altura 1 kg', 'despensa',   189.50),
 (2, 'Té verde 100 g',      'despensa',    75.00),
 (3, 'Molino eléctrico',    'utensilios', 899.00),        -- sin existencias: 409
 (4, 'Prensa francesa',     'utensilios', 420.00),
 (5, 'Máquina industrial',  'equipo',  9999999.00);       -- su total desborda el campo: rollback

INSERT INTO inventario VALUES (1, 500), (2, 300), (3, 0), (4, 40), (5, 25);

INSERT INTO pedidos (id, cliente_id, producto_id, cantidad, total, estado, folio, creado) VALUES
 (1, 2, 2, 1,   75.00, 'PENDIENTE',  NULL,        '2020-01-15 10:00:00'),   -- vencido: la tarea programada lo marca VENCIDO
 (2, 1, 4, 2,  840.00, 'CONFIRMADO', 'CR-1-840', '2026-01-10 09:30:00');
SELECT setval('pedidos_id_seq', 100);
