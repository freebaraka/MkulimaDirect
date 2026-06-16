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

        # 1. Count Total Users (Farmers + Buyers)
        cursor.execute("SELECT (SELECT COUNT(*) FROM farmer) + (SELECT COUNT(*) FROM buyer);")
        total_users = cursor.fetchone()[0]

        # 2. Count Total Produce Listings
        cursor.execute("SELECT COUNT(*) FROM produce;")
        total_produce = cursor.fetchone()[0]

        # 3. Revenue set to NULL as requested
        total_revenue = None

        return jsonify({
            "totalUsers": total_users,
            "totalProduce": total_produce,
            "totalRevenue": total_revenue
        }), 200

    except Exception as e:
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