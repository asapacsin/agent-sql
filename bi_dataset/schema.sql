-- RetailCo BI schema (docs/spec/DATA_MODEL.md section 3). Portable between SQLite and PostgreSQL 16.
CREATE TABLE regions (
  region_id INTEGER PRIMARY KEY,
  region_name TEXT NOT NULL UNIQUE,
  country TEXT NOT NULL
);
CREATE TABLE categories (
  category_id INTEGER PRIMARY KEY,
  category_name TEXT NOT NULL,
  parent_category_id INTEGER REFERENCES categories(category_id),
  level TEXT NOT NULL
);
CREATE TABLE products (
  product_id INTEGER PRIMARY KEY,
  product_name TEXT NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(category_id),
  brand TEXT NOT NULL,
  list_price NUMERIC(10,2) NOT NULL,
  launch_date DATE NOT NULL,
  discontinued_date DATE,
  description TEXT
);
CREATE TABLE product_costs (
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  effective_from DATE NOT NULL,
  effective_to DATE,
  unit_cost NUMERIC(10,2) NOT NULL,
  PRIMARY KEY (product_id, effective_from)
);
CREATE TABLE customers (
  customer_id INTEGER PRIMARY KEY,
  customer_name TEXT NOT NULL,
  email TEXT,
  phone TEXT,
  home_region_id INTEGER NOT NULL REFERENCES regions(region_id),
  segment TEXT NOT NULL,
  acquisition_channel TEXT NOT NULL,
  signup_date DATE NOT NULL,
  is_test_account BOOLEAN NOT NULL
);
CREATE TABLE promotions (
  promotion_id INTEGER PRIMARY KEY,
  promotion_name TEXT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  discount_pct NUMERIC(5,2) NOT NULL,
  category_id INTEGER REFERENCES categories(category_id),
  region_id INTEGER REFERENCES regions(region_id)
);
CREATE TABLE orders (
  order_id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
  order_date DATE NOT NULL,
  order_ts TIMESTAMP NOT NULL,
  ship_region_id INTEGER REFERENCES regions(region_id),
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  promotion_id INTEGER REFERENCES promotions(promotion_id)
);
CREATE TABLE order_items (
  order_item_id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(order_id),
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  quantity INTEGER NOT NULL,
  unit_price NUMERIC(10,2) NOT NULL,
  discount_amount NUMERIC(10,2) NOT NULL
);
CREATE TABLE returns (
  return_id INTEGER PRIMARY KEY,
  order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
  return_date DATE NOT NULL,
  quantity_returned INTEGER NOT NULL,
  refund_amount NUMERIC(10,2) NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE transactions (
  transaction_id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(order_id),
  transaction_date DATE NOT NULL,
  transaction_type TEXT NOT NULL,
  amount NUMERIC(10,2) NOT NULL,
  payment_method TEXT NOT NULL
);
CREATE TABLE business_events (
  event_id INTEGER PRIMARY KEY,
  event_type TEXT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  region_id INTEGER REFERENCES regions(region_id),
  category_id INTEGER REFERENCES categories(category_id),
  product_id INTEGER REFERENCES products(product_id),
  description TEXT NOT NULL
);
CREATE TABLE calendar (
  date_key DATE PRIMARY KEY,
  year INTEGER NOT NULL,
  quarter INTEGER NOT NULL,
  month INTEGER NOT NULL,
  iso_week INTEGER NOT NULL,
  day_of_week INTEGER NOT NULL,
  is_holiday BOOLEAN NOT NULL
);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_returns_item ON returns(order_item_id);
CREATE INDEX idx_transactions_order ON transactions(order_id);
