"""
Database access layer wrapping pymongo for the State Park System.

This module exposes a lightweight `Database` class with static
methods that encapsulate MongoDB collection access. Higher-level
domain code should call these methods to avoid scattering raw
`pymongo` calls throughout the codebase.
"""

import pymongo
from datetime import datetime
import uuid

# Configuration
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "park_system_db"

class Database:
    """
    Wrapper for MongoDB operations to maintain abstraction.
    """
    client = pymongo.MongoClient(MONGO_URI)
    db = client[DB_NAME]

    # Collections
    users_col = db["users"]
    parks_col = db["parks"]
    merch_col = db["merchandise"]
    orders_col = db["orders"]
    carts_col = db["carts"]
    tickets_col = db["support_tickets"]
    reservations_col = db["tickets"]
    audit_col = db["audit_logs"]

    @staticmethod
    def get_user(email):
        """Retrieves a user doc and converts it to a generic dict (Factory logic is in services)."""
        return Database.users_col.find_one({"email": email})

    @staticmethod
    def add_user(user_obj):
        Database.users_col.insert_one(user_obj.to_dict())

    @staticmethod
    def get_all_parks():
        return list(Database.parks_col.find())

    @staticmethod
    def update_park_schedule(park_id, schedules_data):
        """Updates the schedule list for a specific park."""
        Database.parks_col.update_one(
            {"park_id": park_id},
            {"$set": {"schedules": schedules_data}}
        )

    @staticmethod
    def get_all_merchandise():
        return list(Database.merch_col.find())

    @staticmethod
    def update_merch_stock(sku, new_qty):
        Database.merch_col.update_one(
            {"sku": sku},
            {"$set": {"stock_quantity": new_qty}}
        )

    @staticmethod
    def add_order(order_dict):
        Database.orders_col.insert_one(order_dict)

    @staticmethod
    def save_cart(user_id, items_list):
        """Persist a user's cart as a list of serializable line-item dicts."""
        Database.carts_col.update_one({'user_id': user_id}, {'$set': {'user_id': user_id, 'items': items_list}}, upsert=True)

    @staticmethod
    def get_cart(user_id):
        """Retrieve a user's saved cart (dict) or None."""
        return Database.carts_col.find_one({'user_id': user_id})

    @staticmethod
    def delete_cart(user_id):
        Database.carts_col.delete_one({'user_id': user_id})

    @staticmethod
    def create_ticket(owner_id, park_id, park_name, visit_date, price):
        """Persist a ticket (reservation) and return its ticket_id and document."""
        ticket_id = str(uuid.uuid4())[:8]
        doc = {
            "ticket_id": ticket_id,
            "owner_id": owner_id,
            "park_id": park_id,
            "park_name": park_name,
            "visit_date": visit_date,
            "status": "CONFIRMED",
            "qr_code": f"QR-{ticket_id}",
            "price": price,
            "created_at": datetime.now()
        }
        Database.reservations_col.insert_one(doc)
        return ticket_id, doc

    @staticmethod
    def update_ticket_status(ticket_id, status):
        Database.reservations_col.update_one({"ticket_id": ticket_id}, {"$set": {"status": status}})

    @staticmethod
    def atomic_book_spots(park_id, visit_date, qty):
        """
        Atomically attempt to increment schedule.current_occupancy by qty.
        Returns:
          True  -> success
          False -> insufficient capacity
          None  -> park/schedule not found
        """
        park = Database.parks_col.find_one({"park_id": park_id})
        if not park:
            return None
        # find schedule
        # Park-level capacity applies to schedules
        park_max = int(park.get('max_capacity', 0))
        for s in park.get("schedules", []):
            if s.get("visit_date") == visit_date:
                cur = int(s.get("current_occupancy", 0))
                if cur + qty > park_max:
                    return False
                # attempt conditional update: only succeed if current_occupancy still equals cur
                res = Database.parks_col.find_one_and_update(
                    {"park_id": park_id, "schedules": {"$elemMatch": {"visit_date": visit_date, "current_occupancy": cur}}},
                    {"$inc": {"schedules.$.current_occupancy": qty}}
                )
                return res is not None
        return None

    @staticmethod
    def decrement_schedule_occupancy(park_id, visit_date, qty):
        """Atomically decrement occupancy by qty for a given park schedule. Returns True if success."""
        park = Database.parks_col.find_one({"park_id": park_id})
        if not park:
            return False
        for s in park.get("schedules", []):
            if s.get("visit_date") == visit_date:
                cur = int(s.get("current_occupancy", 0))
                new = max(0, cur - qty)
                res = Database.parks_col.update_one(
                    {"park_id": park_id, "schedules.visit_date": visit_date},
                    {"$set": {"schedules.$.current_occupancy": new}}
                )
                return res.modified_count > 0
        return False
    
    @staticmethod
    def get_all_orders():
        return list(Database.orders_col.find())

    @staticmethod
    def add_support_ticket(ticket_dict):
        Database.tickets_col.insert_one(ticket_dict)
    
    @staticmethod
    def get_open_support_tickets():
        return list(Database.tickets_col.find({"status": "OPEN"}))

    @staticmethod
    def update_support_ticket(ticket_id, resolution):
        Database.tickets_col.update_one(
            {"id": ticket_id},
            {"$set": {"status": "RESOLVED", "resolution": resolution}}
        )

    @staticmethod
    def log_audit(entry):
        Database.audit_col.insert_one(entry)

    @staticmethod
    def get_audit_logs():
        return list(Database.audit_col.find().sort("timestamp", -1))

    # ==========================
    # DATA SEEDING
    # ==========================
    @staticmethod
    def seed_data():
        if Database.users_col.count_documents({}) == 0:
            print("--- Seeding MongoDB with Initial Data ---")
            
            # 1. Users
            users = [
                {"user_id": "admin01", "name": "Super Admin", "email": "admin", "password": "admin123", "role": "Admin"},
                {"user_id": "cust01", "name": "John Doe", "email": "john", "password": "123", "role": "Customer"},
                {"user_id": "cust02", "name": "Jane Smith", "email": "jane", "password": "123", "role": "Customer"}
            ]
            Database.users_col.insert_many(users)

            # 2. Parks & Schedules
            parks = [
                {
                    "park_id": "P01", "name": "Bako National Park", "location": "Sarawak", "description": "Oldest national park.",
                    "max_capacity": 20,
                    "schedules": [
                        {"visit_date": "2025-12-01", "current_occupancy": 0},
                        {"visit_date": "2025-12-02", "current_occupancy": 0}
                    ]
                },
                {
                    "park_id": "P02", "name": "Niah National Park", "location": "Miri", "description": "Famous for caves.",
                    "max_capacity": 50,
                    "schedules": [
                        {"visit_date": "2025-12-01", "current_occupancy": 0}
                    ]
                }
            ]
            Database.parks_col.insert_many(parks)

            # 3. Merchandise
            merch = [
                {"sku": "SKU001", "name": "Park T-Shirt", "price": 25.00, "stock_quantity": 100},
                {"sku": "SKU002", "name": "Souvenir Mug", "price": 15.00, "stock_quantity": 50}
            ]
            Database.merch_col.insert_many(merch)
            print("--- Seeding Complete ---")