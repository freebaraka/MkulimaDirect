import psycopg2
import os
from psycopg2 import OperationalError
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

def get_db_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    try:
        connection = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            database=os.getenv("DB_NAME", "mkulima"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "1234"),
            port=os.getenv("DB_PORT", "5432")
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
        conn.close()