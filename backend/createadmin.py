import os
import sys
import getpass
from datetime import datetime

from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument
from werkzeug.security import generate_password_hash

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017/westlake_insurance")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "westlake_insurance")


def get_db():
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    return client[MONGODB_DB_NAME]


def check_database_connection():
    try:
        get_db()
        return True
    except Exception as e:
        print(f"\nDATABASE CONNECTION ERROR:\n{e}")
        return False


def next_id(db, table):
    doc = db.counters.find_one_and_update(
        {"_id": table},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


def create_user():
    print("\n======================================")
    print(" WESTLAKE INSURANCE USER CREATION")
    print("======================================\n")

    full_name = input("Full Name        : ").strip()
    username = input("Username         : ").strip()
    email = input("Email            : ").strip()
    # Use getpass so the password is never echoed to the terminal or captured
    # in shell history / scrollback.
    password = getpass.getpass("Password         : ")
    confirm = getpass.getpass("Confirm Password : ")

    if not full_name or not username or not password:
        print("\nERROR: Full Name, Username and Password are required.")
        return

    if password != confirm:
        print("\nERROR: Passwords do not match.")
        return

    id_number = input("ID/Passport No   : ").strip()
    gender = input("Gender           : ").strip()
    phone = input("Phone Number     : ").strip()
    kra_pin = input("KRA PIN          : ").strip()
    city = input("City             : ").strip()
    county = input("County           : ").strip()

    if not id_number:
        id_number = "N/A"

    if not gender:
        gender = "Prefer not to say"

    if not phone:
        print("\nPhone Number is required.")
        return

    if not kra_pin:
        kra_pin = "N/A"

    if not city:
        city = "Nairobi"

    if not county:
        county = "Nairobi"

    commission_payout = input(
        "Commission Payout (daily/weekly/monthly) [monthly]: "
    ).strip().lower()

    if commission_payout not in ["daily", "weekly", "monthly"]:
        commission_payout = "monthly"

    role = input("Role (admin/agent) [agent]: ").strip().lower()

    if role not in ["admin", "agent"]:
        role = "agent"

    status = "approved"

    password_hash = generate_password_hash(password)

    try:
        db = get_db()
        if db.users.find_one({"$or": [{"username": username}, {"email": email}]}):
            print("\nERROR: Username or Email already exists.")
            return

        db.users.insert_one({
            "id": next_id(db, "users"),
            "full_name": full_name,
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "role": role,
            "status": status,
            "id_number": id_number,
            "gender": gender,
            "phone": phone,
            "kra_pin": kra_pin,
            "city": city,
            "county": county,
            "commission_payout": commission_payout,
            "flagged": 0,
            "flagged_reason": None,
            "flagged_at": None,
            "underpayment_attempts": 0,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })

        print("\n======================================")
        print(" USER CREATED SUCCESSFULLY")
        print("======================================")
        print(f"Name     : {full_name}")
        print(f"Username : {username}")
        print(f"Role     : {role}")
        print(f"Status   : {status}")
        print("======================================\n")

    except Exception as err:
        print("\nDatabase Error:")
        print(err)


if __name__ == "__main__":

    print("Loading configuration from .env...")

    if not check_database_connection():
        print("\nCould not connect to MongoDB.")
        print("Check the values inside your .env file.")
        sys.exit(1)

    create_user()
