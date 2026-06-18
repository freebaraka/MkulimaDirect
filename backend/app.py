from flask import Flask, request, jsonify
from flask_cors import CORS
# Notice we added check_password_hash here at the end of the line!
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db_connection  

app = Flask(__name__)
CORS(app)

# ----------------------------------------------------
# ROUTE 1: REGISTRATION (You already had this)
# ----------------------------------------------------
@app.route('/api/register', methods=['POST'])
def register_user():
    data = request.json
    
    full_name = data.get('fullName')
    phone = data.get('phone')
    role = data.get('role')
    password = data.get('password')

    if not all([full_name, phone, role, password]):
        return jsonify({"error": "All fields are required!"}), 400

    hashed_password = generate_password_hash(password)

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if role == 'farmer':
            cursor.execute(
                "INSERT INTO farmer (full_name, phone_number, password_hash) VALUES (%s, %s, %s)",
                (full_name, phone, hashed_password)
            )
        elif role == 'buyer':
            cursor.execute(
                "INSERT INTO buyer (full_name, phone_number, password_hash) VALUES (%s, %s, %s)",
                (full_name, phone, hashed_password)
            )
        else:
            return jsonify({"error": "Invalid role selected"}), 400

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Registration successful!"}), 201

    except Exception as e:
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This phone number is already registered."}), 409
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------
# ROUTE 2: LOGIN 
# ----------------------------------------------------
@app.route('/api/login', methods=['POST'])
def login_user():
    data = request.json
    name = data.get('name') 
    password = data.get('password')

    # THE FIX IS HERE: It must check for 'name', not 'phone'
    if not name or not password:
        return jsonify({"error": "Full Name and password are required!"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Check if the user is a Farmer
        cursor.execute("SELECT password_hash FROM farmer WHERE full_name = %s", (name,))
        farmer = cursor.fetchone()
        
        if farmer and check_password_hash(farmer[0], password):
            return jsonify({"message": "Login successful!", "role": "farmer"}), 200

        # 2. Check if the user is a Buyer
        cursor.execute("SELECT password_hash FROM buyer WHERE full_name = %s", (name,))
        buyer = cursor.fetchone()
        
        if buyer and check_password_hash(buyer[0], password):
            return jsonify({"message": "Login successful!", "role": "buyer"}), 200

        # 3. If neither matched
        return jsonify({"error": "Invalid name or password."}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 3: GET ALL USERS (For the Admin Dashboard)
# ----------------------------------------------------
@app.route('/api/users', methods=['GET'])
def get_all_users():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        
        # We use UNION ALL to combine the farmer and buyer tables into one big list!
        # We also sort them so the newest users appear at the top.
        query = """
            SELECT full_name, 'farmer' as role, phone_number, joined_date FROM farmer
            UNION ALL
            SELECT full_name, 'buyer' as role, phone_number, joined_date FROM buyer
            ORDER BY joined_date DESC;
        """
        cursor.execute(query)
        users = cursor.fetchall()

        # Format the raw database data into a clean list of dictionaries
        user_list = []
        for user in users:
            user_list.append({
                "fullName": user[0],
                "role": user[1],
                "phone": user[2],
                # Format the timestamp so it looks nice (e.g., "2026-06-16 11:30")
                "joinedDate": user[3].strftime("%Y-%m-%d %H:%M") if user[3] else "Unknown"
            })

        return jsonify(user_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
    # ----------------------------------------------------
# ROUTE 4: GET DASHBOARD STATS (For Admin)
# ----------------------------------------------------
@app.route('/api/admin/stats', methods=['GET'])
def get_admin_stats():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Count total farmers
        cursor.execute("SELECT COUNT(*) FROM farmer;")
        total_farmers = cursor.fetchone()[0]

        # 2. Count total buyers
        cursor.execute("SELECT COUNT(*) FROM buyer;")
        total_buyers = cursor.fetchone()[0]

        # 3. Count total produce listings
        cursor.execute("SELECT COUNT(*) FROM produce;")
        total_produce = cursor.fetchone()[0]

        # 4. Fetch Recent Activity Log (Simplified to avoid timestamp column issues)
        activity_query = """
            SELECT 'Order Placed' AS type, 'Order #' || order_id || ' (' || order_status || ')' AS description, order_id AS sort_key
            FROM orders
            UNION ALL
            SELECT 'New User', full_name || ' registered as a Farmer', farmer_id
            FROM farmer
            UNION ALL
            SELECT 'New Produce', name || ' listed on the market', produce_id
            FROM produce
            ORDER BY sort_key DESC 
            LIMIT 6;
        """
        cursor.execute(activity_query)
        activities = cursor.fetchall()
        
        recent_activities = []
        for a in activities:
            recent_activities.append({
                "type": a[0],
                "description": a[1],
                "time": "Recent"
            })

        return jsonify({
            "totalUsers": total_farmers + total_buyers,
            "totalFarmers": total_farmers,
            "totalBuyers": total_buyers,
            "totalProduce": total_produce,
            "activities": recent_activities
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 5: ADD NEW PRODUCE (Farmer Dashboard)
# ----------------------------------------------------
@app.route('/api/produce', methods=['POST'])
def add_produce():
    data = request.json

    farmer_name = data.get('farmerName') 
    name = data.get('name')
    description = data.get('description')
    category = data.get('category')
    price = data.get('price')
    unit = data.get('unit')
    quantity = data.get('quantity')

    # Basic validation
    if not all([farmer_name, name, price, unit, quantity]):
        return jsonify({"error": "Please fill in all required fields!"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Look up the farmer's ID using the name they typed
        cursor.execute("SELECT farmer_id FROM farmer WHERE full_name = %s", (farmer_name,))
        farmer = cursor.fetchone()

        if not farmer:
            return jsonify({"error": "Farmer not found. Please ensure you typed your registered name exactly."}), 404

        farmer_id = farmer[0]

        # 2. Insert the new produce into the database attached to this farmer_id
        cursor.execute("""
            INSERT INTO produce (farmer_id, name, description, category, price_per_unit, unit_type, stock_quantity)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (farmer_id, name, description, category, price, unit, quantity))

        conn.commit()
        return jsonify({"message": f"Successfully added {name} to the market!"}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE: GET ALL PRODUCE FOR MARKETPLACE (UPDATED)
# ----------------------------------------------------
@app.route('/api/produce', methods=['GET'])
def get_all_produce():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # Join produce, farmer, and farmer_ratings to get average rating and review count
        query = """
            SELECT 
                p.produce_id,
                p.name,
                p.description,
                p.price_per_unit,
                p.stock_quantity,
                p.unit_type,
                f.full_name,
                f.farmer_location,
                f.phone_number,
                COALESCE(ROUND(AVG(fr.rating_value), 1), 0) AS avg_rating,
                COUNT(fr.rating_value) AS review_count
            FROM produce p
            JOIN farmer f ON p.farmer_id = f.farmer_id
            LEFT JOIN farmer_ratings fr ON f.farmer_id = fr.farmer_id
            GROUP BY 
                p.produce_id,
                p.name,
                p.description,
                p.price_per_unit,
                p.stock_quantity,
                p.unit_type,
                p.listed_date,
                f.full_name,
                f.farmer_location,
                f.phone_number
            ORDER BY p.listed_date DESC;
        """
        cursor.execute(query)
        produce_records = cursor.fetchall()

        produce_list = []
        for row in produce_records:
            produce_list.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "price": float(row[3]),
                "stock": row[4],
                "unit": row[5],
                "farmerName": row[6],
                "location": row[7] if row[7] else "Not Specified",
                "phone": row[8],
                "rating": float(row[9]),
                "reviews": int(row[10])
            })

        return jsonify(produce_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 7: ADD TO CART (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/cart', methods=['POST'])
def add_to_cart():
    data = request.json
    buyer_name = data.get('buyerName')
    produce_id = data.get('produceId')

    if not buyer_name or not produce_id:
        return jsonify({"error": "Buyer name and produce ID are required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Find the buyer's ID using their name
        cursor.execute("SELECT buyer_id FROM buyer WHERE full_name = %s", (buyer_name,))
        buyer = cursor.fetchone()

        if not buyer:
            return jsonify({"error": "Buyer not found. Did you type your registered name correctly?"}), 404

        buyer_id = buyer[0]

        # 2. Insert the item into the cart table
        # We default the quantity to 1 for now. They can change it at checkout.
        cursor.execute("""
            INSERT INTO cart (buyer_id, produce_id, quantity)
            VALUES (%s, %s, %s)
        """, (buyer_id, produce_id, 1))

        conn.commit()
        return jsonify({"message": "Item added to your cart successfully!"}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()    
# ----------------------------------------------------
# ROUTE 8: VIEW CART (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/cart/<buyer_name>', methods=['GET'])
def view_cart(buyer_name):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Find the buyer's ID
        cursor.execute("SELECT buyer_id FROM buyer WHERE full_name = %s", (buyer_name,))
        buyer = cursor.fetchone()

        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404

        buyer_id = buyer[0]

        # 2. Get the cart items linked to this buyer
        query = """
            SELECT c.cart_id, p.name, p.price_per_unit, p.unit_type, c.quantity, (p.price_per_unit * c.quantity) as subtotal
            FROM cart c
            JOIN produce p ON c.produce_id = p.produce_id
            WHERE c.buyer_id = %s
            ORDER BY c.added_at DESC;
        """
        cursor.execute(query, (buyer_id,))
        items = cursor.fetchall()

        cart_list = []
        grand_total = 0

        for item in items:
            subtotal = item[5]
            grand_total += subtotal
            cart_list.append({
                "cartId": item[0],
                "name": item[1],
                "price": item[2],
                "unit": item[3],
                "quantity": item[4],
                "subtotal": subtotal
            })

        return jsonify({"items": cart_list, "grandTotal": grand_total}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 9: REMOVE FROM CART
# ----------------------------------------------------
@app.route('/api/cart/<int:cart_id>', methods=['DELETE'])
def remove_from_cart(cart_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cart WHERE cart_id = %s", (cart_id,))
        conn.commit()
        return jsonify({"message": "Item removed from cart"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 10: PROCESS CHECKOUT (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/checkout', methods=['POST'])
def process_checkout():
    data = request.json
    buyer_name = data.get('buyerName')

    if not buyer_name:
        return jsonify({"error": "Buyer name is required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Buyer ID
        cursor.execute("SELECT buyer_id FROM buyer WHERE full_name = %s", (buyer_name,))
        buyer = cursor.fetchone()
        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404
        buyer_id = buyer[0]

        # 2. Fetch all items currently in this buyer's cart
        cursor.execute("""
            SELECT c.produce_id, c.quantity, p.price_per_unit 
            FROM cart c
            JOIN produce p ON c.produce_id = p.produce_id
            WHERE c.buyer_id = %s
        """, (buyer_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            return jsonify({"error": "Your cart is empty!"}), 400

        # Calculate the Grand Total
        grand_total = sum(item[1] * item[2] for item in cart_items)

        # 3. Create the Master Order in the `orders` table
        cursor.execute("""
            INSERT INTO orders (buyer_id, order_status, total_amount)
            VALUES (%s, 'Pending', %s) RETURNING order_id
        """, (buyer_id, grand_total))
        order_id = cursor.fetchone()[0]

        # 4. Move items from Cart to `order_details`
        for item in cart_items:
            produce_id, quantity, price = item
            subtotal = quantity * price
            cursor.execute("""
                INSERT INTO order_details (order_id, produce_id, quantity, price_at_time_of_order, subtotal)
                VALUES (%s, %s, %s, %s, %s)
            """, (order_id, produce_id, quantity, price, subtotal))
            
            # (Optional but cool: Subtract the quantity from the farmer's stock here!)
            # cursor.execute("UPDATE produce SET stock_quantity = stock_quantity - %s WHERE produce_id = %s", (quantity, produce_id))

        # 5. Create a Pending Payment record
        cursor.execute("""
            INSERT INTO payments (order_id, amount, payment_method, payment_status)
            VALUES (%s, %s, 'M-Pesa (Pending)', 'Pending')
        """, (order_id, grand_total))

        # 6. Wipe the user's cart clean!
        cursor.execute("DELETE FROM cart WHERE buyer_id = %s", (buyer_id,))

        # Save all these steps to the database at the exact same time
        conn.commit()
        return jsonify({"message": f"Order #{order_id} placed successfully! Total: KSh {grand_total}"}), 201

    except Exception as e:
        # If any of the steps above fail, cancel ALL of them so the database doesn't break
        conn.rollback() 
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 11: FARMER DASHBOARD STATS (UPGRADED)
# ----------------------------------------------------
@app.route('/api/farmer/stats/<farmer_name>', methods=['GET'])
def get_farmer_stats(farmer_name):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Farmer ID
        cursor.execute("SELECT farmer_id FROM farmer WHERE full_name = %s", (farmer_name,))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404
        farmer_id = farmer[0]

        # 2. Count Active Listings
        cursor.execute("SELECT COUNT(*) FROM produce WHERE farmer_id = %s", (farmer_id,))
        active_listings = cursor.fetchone()[0]

        # 3. Calculate Total Earnings
        cursor.execute("""
            SELECT COALESCE(SUM(od.subtotal), 0)
            FROM order_details od
            JOIN produce p ON od.produce_id = p.produce_id
            JOIN orders o ON od.order_id = o.order_id
            WHERE p.farmer_id = %s AND o.order_status != 'Cancelled'
        """, (farmer_id,))
        total_earnings = cursor.fetchone()[0]

        # 4. Count Pending Orders
        cursor.execute("""
            SELECT COUNT(DISTINCT o.order_id)
            FROM orders o
            JOIN order_details od ON o.order_id = od.order_id
            JOIN produce p ON od.produce_id = p.produce_id
            WHERE p.farmer_id = %s AND o.order_status = 'Pending'
        """, (farmer_id,))
        pending_orders = cursor.fetchone()[0]

        # 5. NEW: Calculate Average Rating and Total Reviews
        cursor.execute("""
            SELECT COALESCE(AVG(rating_value), 0), COUNT(rating_value)
            FROM farmer_ratings
            WHERE farmer_id = %s
        """, (farmer_id,))
        rating_data = cursor.fetchone()
        avg_rating = round(float(rating_data[0]), 1) # Rounds to 1 decimal place (e.g., 4.5)
        total_reviews = rating_data[1]

        return jsonify({
            "totalEarnings": float(total_earnings),
            "activeListings": active_listings,
            "pendingOrders": pending_orders,
            "averageRating": avg_rating,
            "totalReviews": total_reviews
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 12: FARMER ORDER HISTORY
# ----------------------------------------------------
@app.route('/api/farmer/orders/<farmer_name>', methods=['GET'])
def get_farmer_orders(farmer_name):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Farmer ID
        cursor.execute("SELECT farmer_id FROM farmer WHERE full_name = %s", (farmer_name,))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404
        farmer_id = farmer[0]

        # 2. Fetch ALL order details linked to this farmer's produce
        query = """
            SELECT 
                o.order_id, 
                TO_CHAR(o.order_date, 'YYYY-MM-DD HH24:MI'), 
                p.name, 
                od.quantity, 
                p.unit_type,
                od.subtotal, 
                b.full_name, 
                b.phone_number, 
                o.order_status
            FROM order_details od
            JOIN produce p ON od.produce_id = p.produce_id
            JOIN orders o ON od.order_id = o.order_id
            JOIN buyer b ON o.buyer_id = b.buyer_id
            WHERE p.farmer_id = %s
            ORDER BY o.order_date DESC;
        """
        cursor.execute(query, (farmer_id,))
        orders = cursor.fetchall()

        order_list = []
        for order in orders:
            order_list.append({
                "orderId": order[0],
                "date": order[1],
                "produceName": order[2],
                "quantity": order[3],
                "unit": order[4],
                "subtotal": order[5],
                "buyerName": order[6],
                "buyerPhone": order[7],
                "status": order[8]
            })

        return jsonify(order_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()    
# ----------------------------------------------------
# ROUTE 13: BUYER ORDER HISTORY
# ----------------------------------------------------
@app.route('/api/buyer/orders/<buyer_name>', methods=['GET'])
def get_buyer_orders(buyer_name):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Buyer ID
        cursor.execute("SELECT buyer_id FROM buyer WHERE full_name = %s", (buyer_name,))
        buyer = cursor.fetchone()
        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404
        buyer_id = buyer[0]

        # 2. Fetch ALL order details linked to this buyer
        # We join with the farmer table so the buyer knows who to contact for delivery!
        query = """
            SELECT 
                o.order_id, 
                TO_CHAR(o.order_date, 'YYYY-MM-DD HH24:MI'), 
                p.name, 
                od.quantity, 
                p.unit_type,
                od.subtotal, 
                f.full_name, 
                f.phone_number, 
                o.order_status
            FROM orders o
            JOIN order_details od ON o.order_id = od.order_id
            JOIN produce p ON od.produce_id = p.produce_id
            JOIN farmer f ON p.farmer_id = f.farmer_id
            WHERE o.buyer_id = %s
            ORDER BY o.order_date DESC;
        """
        cursor.execute(query, (buyer_id,))
        orders = cursor.fetchall()

        order_list = []
        for order in orders:
            order_list.append({
                "orderId": order[0],
                "date": order[1],
                "produceName": order[2],
                "quantity": order[3],
                "unit": order[4],
                "subtotal": order[5],
                "farmerName": order[6],
                "farmerPhone": order[7],
                "status": order[8]
            })

        return jsonify(order_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 15: RATE A FARMER (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/rate', methods=['POST'])
def rate_farmer():
    data = request.json
    buyer_name = data.get('buyerName')
    farmer_name = data.get('farmerName')
    rating = data.get('rating')

    if not all([buyer_name, farmer_name, rating]):
        return jsonify({"error": "Missing rating data"}), 400

    try:
        rating_val = int(rating)
        if rating_val < 1 or rating_val > 5:
            return jsonify({"error": "Rating must be between 1 and 5."}), 400
    except ValueError:
        return jsonify({"error": "Invalid rating number."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        
        # 1. Get the IDs for both users
        cursor.execute("SELECT buyer_id FROM buyer WHERE full_name = %s", (buyer_name,))
        buyer = cursor.fetchone()
        
        cursor.execute("SELECT farmer_id FROM farmer WHERE full_name = %s", (farmer_name,))
        farmer = cursor.fetchone()

        if not buyer or not farmer:
            return jsonify({"error": "Could not find buyer or farmer in the database."}), 404
            
        b_id, f_id = buyer[0], farmer[0]

        # 2. Insert the rating (or update it if they already rated this farmer)
        cursor.execute("""
            INSERT INTO farmer_ratings (farmer_id, buyer_id, rating_value)
            VALUES (%s, %s, %s)
            ON CONFLICT (farmer_id, buyer_id) 
            DO UPDATE SET rating_value = EXCLUDED.rating_value;
        """, (f_id, b_id, rating_val))
        
        conn.commit()
        return jsonify({"message": f"Success! You rated {farmer_name} {rating_val}/5."}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 17: GET SPECIFIC FARMER'S PRODUCE
# ----------------------------------------------------
@app.route('/api/farmer/produce/<farmer_name>', methods=['GET'])
def get_farmer_inventory(farmer_name):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        
        # Get Farmer ID
        cursor.execute("SELECT farmer_id FROM farmer WHERE full_name = %s", (farmer_name,))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404
            
        # Fetch their produce
        cursor.execute("""
                SELECT produce_id, name, description, price_per_unit, stock_quantity, unit_type, TO_CHAR(listed_date, 'YYYY-MM-DD')
            FROM produce WHERE farmer_id = %s ORDER BY listed_date DESC
        """, (farmer[0],))
        
        produce_records = cursor.fetchall()
        
        inventory = []
        for p in produce_records:
            inventory.append({
                "id": p[0], "name": p[1], "description": p[2], 
                "price": float(p[3]), "stock": p[4], "unit": p[5], "date": p[6]
            })
            
        return jsonify(inventory), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 18: DELETE PRODUCE LISTING
# ----------------------------------------------------
@app.route('/api/produce/<int:produce_id>', methods=['DELETE'])
def delete_produce(produce_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM produce WHERE produce_id = %s", (produce_id,))
        conn.commit()
        return jsonify({"message": "Produce deleted."}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# START THE SERVER
# ----------------------------------------------------
if __name__ == '__main__':
    print("🚀 Mkulima Direct API is running on http://localhost:5000")
    app.run(debug=True, port=5000)