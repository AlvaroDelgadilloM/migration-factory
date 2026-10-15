-- Base local de prueba para el ejemplo JBoss legado (datos sintéticos, reproducibles: misma semilla = mismos datos).
CREATE TABLE customers (
    id         INTEGER PRIMARY KEY,
    name       VARCHAR(120) NOT NULL,
    email      VARCHAR(160) NOT NULL UNIQUE,
    city       VARCHAR(60)  NOT NULL,
    created_at DATE         NOT NULL
);

CREATE TABLE products (
    id       INTEGER PRIMARY KEY,
    name     VARCHAR(120)  NOT NULL,
    category VARCHAR(40)   NOT NULL,
    price    NUMERIC(10,2) NOT NULL
);

CREATE TABLE orders (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 100),
    status      VARCHAR(20) NOT NULL,
    ordered_at  DATE NOT NULL
);
CREATE INDEX orders_customer ON orders(customer_id);
CREATE INDEX orders_product ON orders(product_id);

SELECT setseed(0.42);

INSERT INTO customers (id, name, email, city, created_at)
SELECT g,
       (ARRAY['Ana','Luis','María','Jorge','Sofía','Carlos','Lucía','Pedro','Elena','Diego'])[1 + (g % 10)] || ' ' ||
       (ARRAY['García','López','Martínez','Hernández','Pérez','Sánchez','Ramírez','Torres','Flores','Rivera'])[1 + ((g / 10) % 10)],
       'cliente' || g || '@ejemplo.test',
       (ARRAY['CDMX','Guadalajara','Monterrey','Puebla','Mérida','Cancún','Querétaro','Tijuana'])[1 + (g % 8)],
       DATE '2018-01-01' + (g % 2000)
FROM generate_series(1, 5000) g;

INSERT INTO products (id, name, category, price)
SELECT g,
       (ARRAY['Noche','Desayuno','Spa','Traslado','Tour','Cena','Upgrade','Late checkout'])[1 + (g % 8)] || ' ' || g,
       (ARRAY['hospedaje','alimentos','bienestar','transporte','experiencias'])[1 + (g % 5)],
       round((50 + random() * 4950)::numeric, 2)
FROM generate_series(1, 300) g;

INSERT INTO orders (customer_id, product_id, quantity, status, ordered_at)
SELECT 1 + floor(random() * 5000)::int,
       1 + floor(random() * 300)::int,
       1 + floor(random() * 10)::int,
       (ARRAY['NEW','PAID','SHIPPED','CANCELLED'])[1 + floor(random() * 4)::int],
       DATE '2023-01-01' + floor(random() * 1000)::int
FROM generate_series(1, 50000);
