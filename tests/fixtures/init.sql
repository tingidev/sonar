-- Seed schema for Sonar postgres-connector integration tests.
-- Shapes are chosen to exercise every scenario in the postgres-connector spec:
--   - UUID PK, TIMESTAMPTZ, NUMERIC columns
--   - single-column and composite primary keys
--   - multi-hop foreign-key chains
--   - TEXT[] (ARRAY) and USER-DEFINED enum types

CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled');

CREATE TABLE users (
    user_id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE addresses (
    address_id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id),
    street TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL
);

CREATE TABLE products (
    product_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE orders (
    order_id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id),
    status order_status NOT NULL DEFAULT 'pending',
    placed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE order_items (
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE tags (
    tag_id SERIAL PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    synonyms TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE product_tags (
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    tag_id INTEGER NOT NULL REFERENCES tags(tag_id),
    PRIMARY KEY (product_id, tag_id)
);

INSERT INTO users (user_id, email, name) VALUES
    ('00000000-0000-0000-0000-000000000001', 'alice@example.com', 'Alice'),
    ('00000000-0000-0000-0000-000000000002', 'bob@example.com', 'Bob'),
    ('00000000-0000-0000-0000-000000000003', 'carol@example.com', 'Carol');

INSERT INTO addresses (user_id, street, city, country) VALUES
    ('00000000-0000-0000-0000-000000000001', '1 Main St', 'Dublin', 'IE'),
    ('00000000-0000-0000-0000-000000000002', '2 Elm Rd', 'Cork', 'IE'),
    ('00000000-0000-0000-0000-000000000003', '3 Oak Ln', 'Galway', 'IE');

INSERT INTO products (name, price) VALUES
    ('Widget', 9.99),
    ('Gadget', 19.99),
    ('Gizmo', 29.99),
    ('Doohickey', 39.99),
    ('Thingamajig', 49.99),
    ('Contraption', 59.99);

INSERT INTO orders (user_id, status) VALUES
    ('00000000-0000-0000-0000-000000000001', 'paid'),
    ('00000000-0000-0000-0000-000000000002', 'pending'),
    ('00000000-0000-0000-0000-000000000003', 'shipped');

INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
    (1, 1, 2, 9.99),
    (1, 2, 1, 19.99),
    (2, 3, 1, 29.99),
    (3, 4, 3, 39.99);

INSERT INTO tags (label, synonyms) VALUES
    ('new', ARRAY['fresh', 'novel']),
    ('sale', ARRAY['discount', 'deal']),
    ('featured', ARRAY['highlight', 'spotlight']);

INSERT INTO product_tags (product_id, tag_id) VALUES
    (1, 1),
    (1, 2),
    (2, 3),
    (3, 2);
