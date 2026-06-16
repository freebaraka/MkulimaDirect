from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash
from db import get_db_connection  # Imports the connection function we made earlier!

app = Flask(__name__)
CORS(app) # Allows your HTML files to communicate with this Python server

@app.route('/api/register', methods=['POST'])
def register_user():
    data = request.json
    
    # Extract data from the frontend
    full_name = data.get('fullName')
    phone = data.get('phone')
    role = data.get('role')
    password = data.get('password')

    # Basic validation
    if not all([full_name, phone, role, password]):
        return jsonify({"error": "All fields are required!"}), 400

    # Securely hash the password so it isn't saved as plain text
    hashed_password = generate_password_hash(password)

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # Insert into the correct table based on the role
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

        # Save the changes to the database
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Registration successful!"}), 201

    except Exception as e:
        # If the phone number already exists, Postgres will throw a unique constraint error
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This phone number is already registered."}), 409
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🚀 Mkulima Direct API is running on http://localhost:5000")
    app.run(debug=True, port=5000)