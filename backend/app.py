from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db_connection  
import re
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ------------
# EMAIL SETUP 
# ------------
GMAIL_ADDRESS = "sirwoossah@gmail.com" 
GMAIL_APP_PASSWORD = "psbf qfry urkm hzud"

def send_email(to_email, subject, body):
    """Sends an email using Gmail's secure SMTP server"""
    try:
        msg = MIMEMultipart()
        msg['From'] = f"Mkulima Direct "
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connect to Gmail
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls() # Secure the connection
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Email successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {str(e)}")
        return False

# Temporary storage for OTPs (Using email as the key instead of phone)
OTP_STORE = {}

NAME_REGEX = re.compile(r'^[A-Za-z]+\s+[A-Za-z]+$')
EMAIL_REGEX = re.compile(r'^[\w\.-]+@[\w\.-]+\.\w+$') # NEW: Validates an email address

app = Flask(__name__)
CORS(app)


def ensure_contact_columns_support_email():
    """Migrates legacy phone_number columns to email and enforces basic email checks."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'farmer' AND column_name = 'phone_number'
        """)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE farmer RENAME COLUMN phone_number TO email")

        cursor.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'buyer' AND column_name = 'phone_number'
        """)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE buyer RENAME COLUMN phone_number TO email")

        cursor.execute("ALTER TABLE farmer ALTER COLUMN email TYPE VARCHAR(255)")
        cursor.execute("ALTER TABLE buyer ALTER COLUMN email TYPE VARCHAR(255)")

        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'farmer_email_must_have_at'
                ) THEN
                    ALTER TABLE farmer
                    ADD CONSTRAINT farmer_email_must_have_at
                    CHECK (POSITION('@' IN email) > 1);
                END IF;
            END $$;
        """)

        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'buyer_email_must_have_at'
                ) THEN
                    ALTER TABLE buyer
                    ADD CONSTRAINT buyer_email_must_have_at
                    CHECK (POSITION('@' IN email) > 1);
                END IF;
            END $$;
        """)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not apply contact-column migration: {e}")
    finally:
        cursor.close()
        conn.close()


ensure_contact_columns_support_email()

# ----------------------------------------------------
# ROUTE: SIGNUP REQUEST OTP 
# ----------------------------------------------------
@app.route('/api/signup/request_otp', methods=['POST'])
def signup_request_otp():
    data = request.json
    full_name = data.get('fullName')
    location = data.get('location')
    email = data.get('email')
    password = data.get('password')

    # 1. Strict Validations
    if not all([full_name, location, email, password]):
        return jsonify({"error": "All fields are required, including location."}), 400

    if not NAME_REGEX.match(full_name):
        return jsonify({"error": "Name must be exactly two words with no numbers or special characters."}), 400

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address containing an '@' symbol."}), 400

    # 2. Generate Code and Store it
    otp_code = str(random.randint(100000, 999999))
    OTP_STORE[email] = {
        "code": otp_code,
        "data": data
    }

    # 3. Send the Email
    subject = "Mkulima Direct Verification Code"
    body = f"Hello {full_name},\n\nYour 6-digit verification code is: {otp_code}\n\nWelcome to Mkulima Direct!"

    if send_email(email, subject, body):
        return jsonify({"message": "Verification code sent to your email!"}), 200
    
    return jsonify({"error": "Failed to send email. Check your console."}), 500

# ----------------------------------------------------
# ROUTE: VERIFY EMAIL & CREATE ACCOUNT
# ----------------------------------------------------
@app.route('/api/signup/verify', methods=['POST'])
def signup_verify():
    email = request.json.get('email') 
    user_code = request.json.get('code')
    role = request.json.get('role')

    stored_data = OTP_STORE.get(email)

    if not stored_data or stored_data['code'] != user_code:
        return jsonify({"error": "Invalid or expired verification code."}), 400

    # If code matches, insert them into the database
    signup_data = stored_data['data']
    full_name = signup_data.get('fullName')
    location = signup_data.get('location')
    password = signup_data.get('password')
    role = (role or signup_data.get('role') or '').lower()

    hashed_password = generate_password_hash(password)

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if role == 'farmer':
            cursor.execute(
                "INSERT INTO farmer (full_name, email, farmer_location, password_hash) VALUES (%s, %s, %s, %s)",
                (full_name, email, location, hashed_password)
            )
        else:
            cursor.execute(
                "INSERT INTO buyer (full_name, email, password_hash) VALUES (%s, %s, %s)",
                (full_name, email, hashed_password)
            )

        conn.commit()
        del OTP_STORE[email] # Clear memory
        return jsonify({"message": "Account verified and created successfully!"}), 200

    except Exception as e:
        conn.rollback()
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This email is already registered."}), 409
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ------------------------
# ROUTE 1: REGISTRATION 
# -----------------------
@app.route('/api/register', methods=['POST'])
def register_user():
    data = request.json
    
    full_name = data.get('fullName')
    email = data.get('email') or data.get('phone')
    role = data.get('role')
    password = data.get('password')

    if not all([full_name, email, role, password]):
        return jsonify({"error": "All fields are required!"}), 400

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    hashed_password = generate_password_hash(password)

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if role == 'farmer':
            cursor.execute(
                "INSERT INTO farmer (full_name, email, password_hash) VALUES (%s, %s, %s)",
                (full_name, email, hashed_password)
            )
        elif role == 'buyer':
            cursor.execute(
                "INSERT INTO buyer (full_name, email, password_hash) VALUES (%s, %s, %s)",
                (full_name, email, hashed_password)
            )
        else:
            return jsonify({"error": "Invalid role selected"}), 400

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Registration successful!"}), 201

    except Exception as e:
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This email is already registered."}), 409
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------
# ROUTE 2: LOGIN (STEP 1 - Check Password & Send OTP)
# ----------------------------------------------------
@app.route('/api/login', methods=['POST'])
def login_user():
    data = request.json
    name = data.get('name') 
    password = data.get('password')

    if not name or not password:
        return jsonify({"error": "Full Name and password are required!"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        user_role = None
        user_email = None

        # 1. Check if the user is a Farmer
        cursor.execute("SELECT password_hash, email FROM farmer WHERE full_name = %s", (name,))
        farmer = cursor.fetchone()
        
        if farmer and check_password_hash(farmer[0], password):
            user_role = "farmer"
            user_email = farmer[1]
        else:
            # 2. Check if the user is a Buyer
            cursor.execute("SELECT password_hash, email FROM buyer WHERE full_name = %s", (name,))
            buyer = cursor.fetchone()
            
            if buyer and check_password_hash(buyer[0], password):
                user_role = "buyer"
                user_email = buyer[1]

        # 3. If password or name is wrong
        if not user_role:
            return jsonify({"error": "Invalid name or password."}), 401
            
        if not user_email:
            return jsonify({"error": "No email associated with this account."}), 400

        # 4. Generate and send the Login OTP
        otp_code = str(random.randint(100000, 999999))
        
        # Store it in memory using a special 'login_' prefix
        OTP_STORE[f"login_{user_email}"] = {
            "code": otp_code,
            "role": user_role,
            "name": name
        }

        subject = "Mkulima Direct - Login Attempt"
        body = f"Hello {name},\n\nSomeone is attempting to log into your account.\nYour 6-digit login code is: {otp_code}\n\nIf this was not you, please secure your account."
        
        if send_email(user_email, subject, body):
            return jsonify({
                "message": "OTP sent to your registered email!", 
                "email": user_email,
                "requireOtp": True
            }), 200
        else:
            return jsonify({"error": "Failed to send login email."}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 2B: LOGIN VERIFY (STEP 2 - Check Code & Grant Access)
# ----------------------------------------------------
@app.route('/api/login/verify', methods=['POST'])
def login_verify():
    data = request.json
    email = data.get('email')
    user_code = data.get('code')

    if not email or not user_code:
         return jsonify({"error": "Email and code are required."}), 400

    # Fetch the code we stored in memory during Step 1
    stored_data = OTP_STORE.get(f"login_{email}")

    if not stored_data or stored_data['code'] != user_code:
        return jsonify({"error": "Invalid or expired login code."}), 400

    # Success! Grab the role so the frontend knows where to redirect
    role = stored_data['role']
    
    # Clear the OTP memory so the code can't be reused
    del OTP_STORE[f"login_{email}"]

    return jsonify({"message": "Login successful!", "role": role}), 200
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
            SELECT full_name, 'farmer' as role, email, joined_date FROM farmer
            UNION ALL
            SELECT full_name, 'buyer' as role, email, joined_date FROM buyer
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


@app.route('/api/farmers', methods=['GET'])
def get_farmers():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT farmer_id, full_name, email, farmer_location, joined_date FROM farmer ORDER BY joined_date DESC")
        farmers = [{
            "userId": r[0],
            "fullName": r[1],
            "email": r[2],
            "location": r[3] if r[3] else "Not set",
            "joinedDate": r[4].strftime("%Y-%m-%d") if r[4] else "Unknown"
        } for r in cursor.fetchall()]
        return jsonify(farmers)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/buyers', methods=['GET'])
def get_buyers():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT buyer_id, full_name, email, delivery_address, joined_date FROM buyer ORDER BY joined_date DESC")
        buyers = [{
            "userId": r[0],
            "fullName": r[1],
            "email": r[2],
            "location": r[3] if r[3] else "Not set",
            "joinedDate": r[4].strftime("%Y-%m-%d") if r[4] else "Unknown"
        } for r in cursor.fetchall()]
        return jsonify(buyers)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/orders', methods=['GET'])
def get_orders():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.order_id, b.full_name, o.total_amount, o.order_status
            FROM orders o JOIN buyer b ON o.buyer_id = b.buyer_id
        """)
        orders = [{
            "fullName": r[1],
            "email": f"Order #{r[0]} - KSh {r[2]}",
            "joinedDate": r[3]
        } for r in cursor.fetchall()]
        return jsonify(orders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/users/<role>/<int:user_id>', methods=['PUT'])
def admin_update_user(role, user_id):
    role = (role or '').lower()
    if role not in ['farmer', 'buyer']:
        return jsonify({"error": "Invalid role."}), 400

    data = request.json or {}
    full_name = data.get('fullName')
    email = data.get('email')
    location = data.get('location')

    if full_name is None and email is None and location is None:
        return jsonify({"error": "No fields provided to update."}), 400

    if full_name is not None and not NAME_REGEX.match(full_name):
        return jsonify({"error": "Name must be exactly two words with no numbers or special characters."}), 400

    if email is not None and not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        table_name = 'farmer' if role == 'farmer' else 'buyer'
        id_column = 'farmer_id' if role == 'farmer' else 'buyer_id'

        update_parts = []
        values = []
        if full_name is not None:
            update_parts.append('full_name = %s')
            values.append(full_name)
        if email is not None:
            update_parts.append('email = %s')
            values.append(email)
        if location is not None:
            location_column = 'farmer_location' if role == 'farmer' else 'delivery_address'
            update_parts.append(f"{location_column} = %s")
            values.append(location)

        values.append(user_id)
        query = f"UPDATE {table_name} SET {', '.join(update_parts)} WHERE {id_column} = %s"
        cursor.execute(query, tuple(values))

        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "User not found."}), 404

        conn.commit()
        return jsonify({"message": "User updated successfully."}), 200
    except Exception as e:
        conn.rollback()
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This email is already registered."}), 409
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/users/<role>/<int:user_id>', methods=['DELETE'])
def admin_delete_user(role, user_id):
    role = (role or '').lower()
    if role not in ['farmer', 'buyer']:
        return jsonify({"error": "Invalid role."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        table_name = 'farmer' if role == 'farmer' else 'buyer'
        id_column = 'farmer_id' if role == 'farmer' else 'buyer_id'

        cursor.execute(f"DELETE FROM {table_name} WHERE {id_column} = %s", (user_id,))
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "User not found."}), 404

        conn.commit()
        return jsonify({"message": "User deleted successfully."}), 200
    except Exception as e:
        conn.rollback()
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
                f.email,
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
                f.email
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
    delivery_address = (data.get('deliveryAddress') or '').strip()

    if not buyer_name or not delivery_address:
        return jsonify({"error": "Buyer name and delivery address are required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Buyer ID and email
        cursor.execute("SELECT buyer_id, email FROM buyer WHERE full_name = %s", (buyer_name,))
        buyer = cursor.fetchone()
        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404
        buyer_id = buyer[0]
        buyer_phone = buyer[1]

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

        # 4. Move items from Cart to `order_details` and keep delivery address per line item
        for item in cart_items:
            produce_id, quantity, price = item
            subtotal = quantity * price
            cursor.execute("""
                INSERT INTO order_details (order_id, produce_id, quantity, price_at_time_of_order, subtotal, delivery_address)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (order_id, produce_id, quantity, price, subtotal, delivery_address))
            
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

        # 7. Send SMS alerts to farmers for each produce item in this order
        for item in cart_items:
            produce_id, quantity, _price = item

            cursor.execute("""
                SELECT f.email, p.name
                FROM farmer f
                JOIN produce p ON f.farmer_id = p.farmer_id
                WHERE p.produce_id = %s
            """, (produce_id,))
            farmer_data = cursor.fetchone()

            if farmer_data:
                farmer_phone = farmer_data[0]
                produce_name = farmer_data[1]

                sms_message = (
                    f"Mkulima Direct: New Order! {buyer_name} ({buyer_phone}) "
                    f"has ordered {quantity} of {produce_name}. Deliver to: {delivery_address}."
                )

                if send_email(farmer_phone, "Mkulima Direct New Order Alert", sms_message):
                    print(f"Email successfully sent to {farmer_phone}")
                else:
                    print("Email Failed to send")

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
                b.email, 
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
                f.email, 
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


@app.route('/api/complaints', methods=['POST'])
def submit_complaint():
    data = request.json
    user_name = data.get('userName')
    user_role = data.get('userRole')
    subject = data.get('subject')
    description = data.get('description')

    if not all([user_name, user_role, subject, description]):
        return jsonify({"error": "All fields are required"}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO complaints (user_name, user_role, subject, description) VALUES (%s, %s, %s, %s)",
            (user_name, user_role, subject, description)
        )
        conn.commit()
        return jsonify({"message": "Complaint submitted successfully!"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/complaints', methods=['GET'])
def get_complaints():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_name, user_role, subject, description, status FROM complaints ORDER BY created_at DESC")
        complaints = [
            {
                "userName": r[0],
                "userRole": r[1],
                "subject": r[2],
                "description": r[3],
                "status": r[4]
            }
            for r in cursor.fetchall()
        ]
        return jsonify(complaints)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# START THE SERVER
# ----------------------------------------------------
if __name__ == '__main__':
    print("Mkulima Direct API is running on http://localhost:5000")
    app.run(debug=True, port=5000)