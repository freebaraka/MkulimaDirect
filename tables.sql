CREATE DATABASE mkulima;
\c mkulima;

-- 1. FARMER TABLE
CREATE TABLE farmer (
    farmer_id SERIAL PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL CHECK (POSITION('@' IN email) > 1),
    farmer_location VARCHAR(255),
    password_hash VARCHAR(255) NOT NULL,
    joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. BUYER TABLE
CREATE TABLE buyer (
    buyer_id SERIAL PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL CHECK (POSITION('@' IN email) > 1),
    delivery_address TEXT,
    password_hash VARCHAR(255) NOT NULL,
    joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. ADMIN TABLE
CREATE TABLE admin (
    admin_id SERIAL PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role_level VARCHAR(50) DEFAULT 'Moderator',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. PRODUCE TABLE 
CREATE TABLE produce (
    produce_id SERIAL PRIMARY KEY,
    farmer_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    category VARCHAR(100), 
    price_per_unit DECIMAL(10, 2) NOT NULL,
    unit_type VARCHAR(50) NOT NULL, 
    stock_quantity INT NOT NULL DEFAULT 0,
    -- image_url VARCHAR(255),  (Commented out as requested)
    listed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (farmer_id) REFERENCES farmer(farmer_id) ON DELETE CASCADE
);

-- 5. CART TABLE 
CREATE TABLE cart (
    cart_id SERIAL PRIMARY KEY,
    buyer_id INT NOT NULL,
    produce_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (buyer_id) REFERENCES buyer(buyer_id) ON DELETE CASCADE,
    FOREIGN KEY (produce_id) REFERENCES produce(produce_id) ON DELETE CASCADE
);

-- 6. ORDERS TABLE 
CREATE TABLE orders (
    order_id SERIAL PRIMARY KEY,
    buyer_id INT NOT NULL,
    order_status VARCHAR(50) DEFAULT 'Pending', 
    total_amount DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (buyer_id) REFERENCES buyer(buyer_id) ON DELETE RESTRICT
);

-- 7. ORDER DETAILS TABLE 
CREATE TABLE order_details (
    order_detail_id SERIAL PRIMARY KEY,
    order_id INT NOT NULL,
    produce_id INT NOT NULL,
    quantity INT NOT NULL,
    price_at_time_of_order DECIMAL(10, 2) NOT NULL, 
    subtotal DECIMAL(10, 2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    FOREIGN KEY (produce_id) REFERENCES produce(produce_id) ON DELETE RESTRICT
);

-- 8. PAYMENTS TABLE
CREATE TABLE payments (
    payment_id SERIAL PRIMARY KEY,
    order_id INT NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    payment_method VARCHAR(50) NOT NULL, 
    transaction_reference VARCHAR(100) UNIQUE, 
    payment_status VARCHAR(50) DEFAULT 'Pending', 
    payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

-- Verify everything was created successfully
\dt

-- Expanding Display
\x

--  To be able to store quantities depending on the type......
ALTER TABLE produce 
ALTER COLUMN stock_quantity TYPE VARCHAR(100);

CREATE TABLE farmer_ratings (
    rating_id SERIAL PRIMARY KEY,
    farmer_id INT NOT NULL REFERENCES farmer(farmer_id) ON DELETE CASCADE,
    buyer_id INT NOT NULL REFERENCES buyer(buyer_id) ON DELETE CASCADE,
    rating_value INT NOT NULL CHECK (rating_value >= 1 AND rating_value <= 5),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (farmer_id, buyer_id) 
);

Select * from farmer;
-- Complaints table
CREATE TABLE complaints (
    complaint_id SERIAL PRIMARY KEY,
    user_name VARCHAR(255) NOT NULL,
    user_role VARCHAR(50) NOT NULL, -- 'farmer' or 'buyer'
    subject VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'Pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- updating table orders because of the delivery address and phone number
-- 1. Add the new columns
ALTER TABLE orders ADD COLUMN delivery_address TEXT DEFAULT 'Not Provided';
ALTER TABLE orders ADD COLUMN buyer_phone VARCHAR(20) DEFAULT '0000000000';

-- 2. Make them NOT NULL (if you want to enforce data entry)
ALTER TABLE orders ALTER COLUMN delivery_address SET NOT NULL;
ALTER TABLE orders ALTER COLUMN buyer_phone SET NOT NULL;

-- 3. If you need to change the foreign key constraint:
ALTER TABLE orders DROP CONSTRAINT orders_buyer_id_fkey;
ALTER TABLE orders ADD CONSTRAINT orders_buyer_id_fkey FOREIGN KEY (buyer_id) REFERENCES buyer(buyer_id);

ALTER TABLE produce ADD COLUMN available_quantity INT DEFAULT 0;