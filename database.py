"""
Database access layer wrapping pymongo for the State Park System.

This module exposes a lightweight `Database` class with static
methods that encapsulate MongoDB collection access. Higher-level
domain code should call these methods to avoid scattering raw
`pymongo` calls throughout the codebase.
"""

import pymongo
from datetime import datetime, timezone
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
    def get_user_by_id(user_id):
        """Retrieve a user document by its `user_id` field."""
        if not user_id:
            return None
        return Database.users_col.find_one({"user_id": user_id})

    @staticmethod
    def add_user(user_obj):
        Database.users_col.insert_one(user_obj.to_dict())

    @staticmethod
    def update_user_profile(user_id, profile_fields: dict):
        """Update top-level profile/demographic fields for a user by user_id."""
        Database.users_col.update_one({'user_id': user_id}, {'$set': profile_fields})

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
                {"user_id": "cust01", "name": "John Doe", "email": "john", "password": "123", "role": "Customer", "age_group": "25-34", "gender": "Male", "region": "Sarawak", "visitor_type": "local", "marketing_opt_in": True},
                {"user_id": "cust02", "name": "Jane Smith", "email": "jane", "password": "123", "role": "Customer", "age_group": "35-44", "gender": "Female", "region": "Sarawak", "visitor_type": "domestic", "marketing_opt_in": False},
                {"user_id": "cust03", "name": "Alice Park", "email": "alice", "password": "pw3", "role": "Customer", "age_group": "18-24", "gender": "Female", "region": "Miri", "visitor_type": "tourist", "marketing_opt_in": True},
                {"user_id": "cust04", "name": "Bob Rivers", "email": "bob", "password": "pw4", "role": "Customer", "age_group": "45-54", "gender": "Male", "region": "Miri", "visitor_type": "local", "marketing_opt_in": False},
                {"user_id": "cust05", "name": "Carol Lake", "email": "carol", "password": "pw5", "role": "Customer", "age_group": "35-44", "gender": "Female", "region": "Kuching", "visitor_type": "domestic", "marketing_opt_in": True},
                {"user_id": "cust06", "name": "Dave Hill", "email": "dave", "password": "pw6", "role": "Customer", "age_group": "25-34", "gender": "Male", "region": "Kuching", "visitor_type": "local", "marketing_opt_in": False},
                {"user_id": "cust07", "name": "Eve Forrest", "email": "eve", "password": "pw7", "role": "Customer", "age_group": "55+", "gender": "Female", "region": "Labuan", "visitor_type": "tourist", "marketing_opt_in": False}
            ]
            Database.users_col.insert_many(users)

            # 2. Parks & Schedules
            parks = [
                {
                    "park_id": "P01", "name": "Bako National Park", "location": "Sarawak", "description": "Oldest national park.",
                    "max_capacity": 20,
                    "ticket_price": 10.00,
                    "schedules": [
                        {"visit_date": "2025-12-01", "current_occupancy": 0},
                        {"visit_date": "2025-12-02", "current_occupancy": 0}
                    ]
                },
                {
                    "park_id": "P02", "name": "Niah National Park", "location": "Miri", "description": "Famous for caves.",
                    "max_capacity": 50,
                    "ticket_price": 15.00,
                    "schedules": [
                        {"visit_date": "2025-12-01", "current_occupancy": 0}
                    ]
                }
            ]
            Database.parks_col.insert_many(parks)

            # 3. Merchandise
            merch = [
                {"sku": "SKU001", "name": "Park T-Shirt", "price": 25.00, "stock_quantity": 100},
                {"sku": "SKU002", "name": "Souvenir Mug", "price": 15.00, "stock_quantity": 50},
                {"sku": "SKU003", "name": "Windbreaker Jacket", "price": 55.00, "stock_quantity": 40},
                {"sku": "SKU004", "name": "Hiking Cap", "price": 12.00, "stock_quantity": 200},
                {"sku": "SKU005", "name": "Camping Mug", "price": 18.00, "stock_quantity": 80},
                {"sku": "SKU006", "name": "Trail Map (Folded)", "price": 5.00, "stock_quantity": 150},
                {"sku": "SKU007", "name": "Sticker Pack", "price": 4.00, "stock_quantity": 500},
                {"sku": "SKU008", "name": "Outdoor Blanket", "price": 45.00, "stock_quantity": 30},
                {"sku": "SKU009", "name": "Water Bottle", "price": 20.00, "stock_quantity": 120},
                {"sku": "SKU010", "name": "Binoculars (Compact)", "price": 75.00, "stock_quantity": 15}
            ]
            Database.merch_col.insert_many(merch)

            # 4. Sample reservations (tickets) and orders for analytics demo
            # Create several ticket reservations across parks/dates for different users
            reservations = []
            # use fixed dates matching seeded schedules
            reservations.append({
                "ticket_id": str(uuid.uuid4())[:8], "owner_id": "cust01", "park_id": "P01", "park_name": "Bako National Park",
                "visit_date": "2025-12-01", "status": "CONFIRMED", "qr_code": "QR-" + str(uuid.uuid4())[:8], "price": 10.00, "created_at": datetime(2025, 11, 20, 13, 0, 0, tzinfo=timezone.utc).isoformat(timespec='milliseconds')
            })
            reservations.append({
                "ticket_id": str(uuid.uuid4())[:8], "owner_id": "cust02", "park_id": "P01", "park_name": "Bako National Park",
                "visit_date": "2025-12-02", "status": "CONFIRMED", "qr_code": "QR-" + str(uuid.uuid4())[:8], "price": 10.00, "created_at": datetime(2025, 11, 22, 10, 30, 0, tzinfo=timezone.utc).isoformat(timespec='milliseconds')
            })
            reservations.append({
                "ticket_id": str(uuid.uuid4())[:8], "owner_id": "cust03", "park_id": "P02", "park_name": "Niah National Park",
                "visit_date": "2025-12-01", "status": "CONFIRMED", "qr_code": "QR-" + str(uuid.uuid4())[:8], "price": 15.00, "created_at": datetime(2025, 11, 23, 9, 15, 0, tzinfo=timezone.utc).isoformat(timespec='milliseconds')
            })
            reservations.append({
                "ticket_id": str(uuid.uuid4())[:8], "owner_id": "cust04", "park_id": "P02", "park_name": "Niah National Park",
                "visit_date": "2025-12-01", "status": "CONFIRMED", "qr_code": "QR-" + str(uuid.uuid4())[:8], "price": 15.00, "created_at": datetime(2025, 11, 24, 14, 45, 0, tzinfo=timezone.utc).isoformat(timespec='milliseconds')
            })
            reservations.append({
                "ticket_id": str(uuid.uuid4())[:8], "owner_id": "cust05", "park_id": "P01", "park_name": "Bako National Park",
                "visit_date": "2025-12-01", "status": "CONFIRMED", "qr_code": "QR-" + str(uuid.uuid4())[:8], "price": 10.00, "created_at": datetime(2025, 11, 25, 8, 0, 0, tzinfo=timezone.utc).isoformat(timespec='milliseconds')
            })
            Database.reservations_col.insert_many(reservations)

            # Sample orders combining tickets and merchandise
            orders = []
            # Order 1: cust01 buys 1 ticket and a T-Shirt
            orders.append({
                "order_id": str(uuid.uuid4())[:8], "user_id": "cust01",
                "line_items": [
                    {"item_type": "TICKET", "item_name": "Bako National Park", "quantity": 1, "unit_price": 10.00, "metadata": {"park_name": "Bako National Park", "park_id": "P01", "date": "2025-12-01"}},
                    {"item_type": "MERCH", "item_name": "Park T-Shirt", "quantity": 1, "unit_price": 25.00, "metadata": {"sku": "SKU001"}}
                ],
                "total_cost": 35.00, "date": datetime(2025,11,20,13,51,2,739000, tzinfo=timezone.utc).isoformat(timespec='milliseconds'), "payment_status": "PAID"
            })
            # Order 2: cust02 buys 2 mugs
            orders.append({
                "order_id": str(uuid.uuid4())[:8], "user_id": "cust02",
                "line_items": [
                    {"item_type": "MERCH", "item_name": "Souvenir Mug", "quantity": 2, "unit_price": 15.00, "metadata": {"sku": "SKU002"}}
                ],
                "total_cost": 30.00, "date": datetime(2025,11,22,11,20,0,0, tzinfo=timezone.utc).isoformat(timespec='milliseconds'), "payment_status": "PAID"
            })
            # Order 3: cust03 ticket + blanket
            orders.append({
                "order_id": str(uuid.uuid4())[:8], "user_id": "cust03",
                "line_items": [
                    {"item_type": "TICKET", "item_name": "Niah National Park", "quantity": 1, "unit_price": 15.00, "metadata": {"park_name": "Niah National Park", "park_id": "P02", "date": "2025-12-01"}},
                    {"item_type": "MERCH", "item_name": "Outdoor Blanket", "quantity": 1, "unit_price": 45.00, "metadata": {"sku": "SKU008"}}
                ],
                "total_cost": 60.00, "date": datetime(2025,11,23,9,0,0,0, tzinfo=timezone.utc).isoformat(timespec='milliseconds'), "payment_status": "PAID"
            })
            # Order 4: cust04 buys binoculars
            orders.append({
                "order_id": str(uuid.uuid4())[:8], "user_id": "cust04",
                "line_items": [
                    {"item_type": "MERCH", "item_name": "Binoculars (Compact)", "quantity": 1, "unit_price": 75.00, "metadata": {"sku": "SKU010"}}
                ],
                "total_cost": 75.00, "date": datetime(2025,11,24,15,30,0,0, tzinfo=timezone.utc).isoformat(timespec='milliseconds'), "payment_status": "PAID"
            })
            # Order 5: cust05 buys ticket + water bottle
            orders.append({
                "order_id": str(uuid.uuid4())[:8], "user_id": "cust05",
                "line_items": [
                    {"item_type": "TICKET", "item_name": "Bako National Park", "quantity": 1, "unit_price": 10.00, "metadata": {"park_name": "Bako National Park", "park_id": "P01", "date": "2025-12-01"}},
                    {"item_type": "MERCH", "item_name": "Water Bottle", "quantity": 1, "unit_price": 20.00, "metadata": {"sku": "SKU009"}}
                ],
                "total_cost": 30.00, "date": datetime(2025,11,25,8,30,0,0, tzinfo=timezone.utc).isoformat(timespec='milliseconds'), "payment_status": "PAID"
            })
            # Order 6: cust06 mixed order (two merch)
            orders.append({
                "order_id": str(uuid.uuid4())[:8], "user_id": "cust06",
                "line_items": [
                    {"item_type": "MERCH", "item_name": "Hiking Cap", "quantity": 2, "unit_price": 12.00, "metadata": {"sku": "SKU004"}},
                    {"item_type": "MERCH", "item_name": "Trail Map (Folded)", "quantity": 3, "unit_price": 5.00, "metadata": {"sku": "SKU006"}}
                ],
                "total_cost": 39.00, "date": datetime(2025,11,26,12,0,0,0, tzinfo=timezone.utc).isoformat(timespec='milliseconds'), "payment_status": "PAID"
            })
            # Order 7: cust07 ticket only
            orders.append({
                "order_id": str(uuid.uuid4())[:8], "user_id": "cust07",
                "line_items": [
                    {"item_type": "TICKET", "item_name": "Niah National Park", "quantity": 2, "unit_price": 15.00, "metadata": {"park_name": "Niah National Park", "park_id": "P02", "date": "2025-12-01"}}
                ],
                "total_cost": 30.00, "date": datetime(2025,11,24,16,10,0,0, tzinfo=timezone.utc).isoformat(timespec='milliseconds'), "payment_status": "PAID"
            })

            Database.orders_col.insert_many(orders)
            print("--- Seeding Complete ---")