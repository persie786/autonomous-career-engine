from utils.auth import create_user
from utils.user_context import set_current_user
from database.db_handler import get_connection, init_db

MY_EMAIL = "shahahmed1422@gmail.com"  # change this
MY_PASSWORD = "03124404882"  # change this — you'll log in with it

init_db()
set_current_user(0)
my_user_id = create_user(MY_EMAIL, MY_PASSWORD)
print(f"Created account id={my_user_id}")

conn = get_connection()
cursor = conn.cursor()
for table in ("jobs", "activity_log"):
    cursor.execute(
        f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (my_user_id,)
    )
    print(f"{table}: {cursor.rowcount} row(s) backfilled")
conn.commit()
conn.close()
print("Done — log in with the email/password set above.")
