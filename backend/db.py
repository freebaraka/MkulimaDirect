import psycopg2
from psycopg2 import OperationalError
import os


def _load_env():
    """
    Load environment variables from project-level .env first, then backend/.env.

    Existing process environment values are preserved (not overwritten), which
    keeps production/container-injected secrets authoritative over local files.
    """
    backend_dir = os.path.dirname(__file__)
    project_root = os.path.dirname(backend_dir)
    for env_path in (os.path.join(project_root, '.env'), os.path.join(backend_dir, '.env')):
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, 'r', encoding='utf-8') as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except Exception:
            pass


_load_env()

def get_db_connection():
    """
    Create and return a PostgreSQL connection using environment-driven config.

    Returns:
    - psycopg2 connection object when successful
    - None when connection fails (caller decides HTTP error behavior)
    """
    try:
        connection = psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            database=os.getenv('DB_NAME', 'mkulima'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '1234'),
            port=os.getenv('DB_PORT', '5432')
        )
        return connection
    except OperationalError as e:
        print(f"Database connection failed: {e}")
        return None

# Local smoke-test entrypoint for quick manual DB connectivity checks.
if __name__ == "__main__":
    conn = get_db_connection()
    if conn:
        print("🚀 Connected to the Mkulima Direct database successfully!")
        conn.close() # Always close the connection when done testing