import psycopg2
from psycopg2 import OperationalError

def get_db_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    try:
        connection = psycopg2.connect(
            host="localhost",
            database="mkulima",
            user="postgres",
            password="1234", 
            port="5432"
        )
        return connection
    except OperationalError as e:
        print(f"Database connection failed: {e}")
        return None

# Quick test to see if it works!
if __name__ == "__main__":
    conn = get_db_connection()
    if conn:
        print("🚀 Connected to the Mkulima Direct database successfully!")
        conn.close() # Always close the connection when done testing