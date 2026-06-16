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
# ROUTE 2: LOGIN (This is where the new code goes!)
# ----------------------------------------------------
@app.route('/api/login', methods=['POST'])
def login_user():
    data = request.json
    phone = data.get('phone')
    password = data.get('password')

    if not phone or not password:
        return jsonify({"error": "Phone and password are required!"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Check if the user is a Farmer
        cursor.execute("SELECT password_hash FROM farmer WHERE phone_number = %s", (phone,))
        farmer = cursor.fetchone()
        
        # If found, check if the typed password matches the saved hash
        if farmer and check_password_hash(farmer[0], password):
            return jsonify({"message": "Login successful!", "role": "farmer"}), 200

        # 2. Check if the user is a Buyer
        cursor.execute("SELECT password_hash FROM buyer WHERE phone_number = %s", (phone,))
        buyer = cursor.fetchone()
        
        if buyer and check_password_hash(buyer[0], password):
            return jsonify({"message": "Login successful!", "role": "buyer"}), 200

        # 3. If neither matched, the credentials are wrong
        return jsonify({"error": "Invalid phone number or password."}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        # We put this in a 'finally' block to ensure the database connection 
        # always closes, even if an error happens!
        cursor.close()
        conn.close()


# ----------------------------------------------------
# START THE SERVER
# ----------------------------------------------------
if __name__ == '__main__':
    print("🚀 Mkulima Direct API is running on http://localhost:5000")
    app.run(debug=True, port=5000)