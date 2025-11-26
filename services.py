"""
Application services and utilities.

This module contains service-layer components used across the
application: auditing, refund policy orchestration, authentication
and a simple admin console facade. Services should coordinate
domain objects and the Database wrapper, avoiding direct UI code.
"""

import uuid
from datetime import datetime, timedelta
from database import Database
from models import Customer, Admin, Order, Ticket, SupportTicket, Park, Schedule, Merchandise

# ==========================
# AUDIT LOG
# ==========================
class AuditLog:
    """Simple audit logger that writes structured entries to the DB.

    The `log` method creates a timestamped entry and persists it via
    the `Database.log_audit` helper. `get_logs` reads back entries.
    """

    @staticmethod
    def log(user_name, category, action):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "timestamp": timestamp,
            "category": category,
            "user": user_name,
            "action": action
        }
        Database.log_audit(entry)

    @staticmethod
    def get_logs():
        return Database.get_audit_logs()

# ==========================
# STRATEGY PATTERN (REFUND)
# ==========================
class RefundStrategy:
    """Policy object determining refund eligibility.

    This trivial strategy currently allows refunds if the visit date
    is more than 24 hours away. Replace or extend this strategy to
    implement different refund policies.
    """

    def is_refundable(self, visit_date_str):
        visit_date = datetime.strptime(visit_date_str, "%Y-%m-%d")
        if visit_date - datetime.now() > timedelta(hours=24):
            return True
        return False

class RefundRequest:
    """Orchestrates a refund attempt for a ticket using the selected policy.

    The `process_refund` method applies the policy, updates persistent
    ticket state, decrements occupancy and logs auditing information.
    It returns True on success, False when policy denies refund.
    """

    def __init__(self, ticket, customer):
        self.ticket = ticket
        self.customer = customer
        self.strategy = RefundStrategy()

    def process_refund(self):
        if self.strategy.is_refundable(self.ticket.visit_date):
            # Update persistent ticket status
            try:
                Database.update_ticket_status(self.ticket.ticket_id, "CANCELLED")
            except Exception:
                pass

            # Decrement schedule occupancy in DB (use park_id if available)
            try:
                park_id = getattr(self.ticket, 'park_id', None)
                if park_id:
                    Database.decrement_schedule_occupancy(park_id, self.ticket.visit_date, 1)
            except Exception:
                pass

            # Remove from customer's session tickets if present
            try:
                self.customer.tickets.remove(self.ticket)
            except ValueError:
                pass

            AuditLog.log(self.customer.name, "PAYMENT", f"Refund processed ${self.ticket.price}")
            return True
        else:
            AuditLog.log(self.customer.name, "PAYMENT", "Refund denied (Policy)")
            return False

# ==========================
# AUTH & FACADE
# ==========================
class AuthenticationManager:
    """Singleton-like facade for simple user authentication.

    Responsibilities:
    - Validate credentials against users stored in the DB
    - Create `Customer` or `Admin` domain objects on successful login
    - Track the currently-logged-in user for audit purposes
    """

    _instance = None
    current_user = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AuthenticationManager, cls).__new__(cls)
        return cls._instance

    def login(self, email, password):
        data = Database.get_user(email)
        if data and data['password'] == password:
            if data['role'] == 'Admin':
                user = Admin(data['user_id'], data['name'], data['email'], data['password'])
            else:
                # Pass demographic/profile fields into Customer if present
                user = Customer(
                    data['user_id'], data['name'], data['email'], data['password'],
                    age_group=data.get('age_group'),
                    gender=data.get('gender'),
                    region=data.get('region'),
                    visitor_type=data.get('visitor_type'),
                    marketing_opt_in=data.get('marketing_opt_in', False)
                )
            
            self.current_user = user
            AuditLog.log(user.name, "USER", "Logged In")
            return user
        return None

    def logout(self):
        if self.current_user:
            AuditLog.log(self.current_user.name, "USER", "Logged Out")
            self.current_user = None

    def register_customer(self, name, email, password):
        if Database.get_user(email):
            return False
        # Generate a sequential customer id in format custXX
        # Count existing customers and add 1 (pad to 2 digits)
        try:
            count = Database.users_col.count_documents({"role": "Customer"})
        except Exception:
            # Fallback if direct collection access isn't available
            count = 0
        new_num = count + 1
        user_id = f"cust{new_num:02d}"
        new_user = Customer(user_id, name, email, password)
        Database.add_user(new_user)
        AuditLog.log(name, "USER", "Registered new account")
        return True

class AdminConsole:
    """Facade for Admin Operations"""
    

    def manage_park(self, admin_user):
        """Top-level park management menu: add, edit, delete, list parks."""
        while True:
            print("\n--- Manage Parks ---")
            print("1. Add Park")
            print("2. Edit Park")
            print("3. Delete Park")
            print("4. List Parks")
            print("0. Back")
            choice = input("Select (number, 0 to go back): ").strip()
            if choice == '0':
                return

            elif choice == '1':
                # Interactive: collect park details here and delegate persistence to add_park()
                print("\n--- Add Park ---")
                # Name
                while True:
                    name = input("Name: ").strip()
                    if name:
                        break
                    print("Name cannot be empty.")

                # Location
                while True:
                    loc = input("Location: ").strip()
                    if loc:
                        break
                    print("Location cannot be empty.")

                # Description
                desc = input("Description: ").strip()

                # Park-level max capacity
                while True:
                    try:
                        maxc = int(input("Park max capacity (positive integer): ").strip())
                        if maxc <= 0:
                            print("Max capacity must be a positive integer.")
                            continue
                        break
                    except Exception:
                        print("Enter a valid integer for max capacity.")

                # Ticket price for this park (required)
                while True:
                    try:
                        tprice_in = input("Ticket price (e.g. 12.50): ").strip()
                        if tprice_in == '':
                            print("Ticket price is required.")
                            continue
                        ticket_price = float(tprice_in)
                        if ticket_price < 0:
                            print("Ticket price must be non-negative.")
                            continue
                        break
                    except Exception:
                        print("Enter a valid numeric price (e.g. 12.50).")

                # Prompt for schedules
                try:
                    num_sched = int(input("How many schedules to add (0 for none)? "))
                except Exception:
                    num_sched = 0

                scheds = []
                for i in range(num_sched):
                    while True:
                        date = input(f"Schedule {i+1} - Date (YYYY-MM-DD): ").strip()
                        if not date:
                            print("Date cannot be empty.")
                            continue
                        try:
                            datetime.strptime(date, "%Y-%m-%d")
                        except Exception:
                            print("Invalid date format. Use YYYY-MM-DD.")
                            continue
                        break
                    scheds.append(Schedule(date))

                try:
                    park = Park.add_park(name, loc, desc, schedules=scheds, max_capacity=maxc, ticket_price=ticket_price)
                    AuditLog.log(admin_user.name, "SYSTEM", f"Added Park {name} ({park.park_id})")
                    print(f"Park {name} ({park.park_id}) added.")
                except Exception as e:
                    print(f"Failed to add park: {e}")
                continue

            elif choice == '2':
                parks = Database.get_all_parks()
                if not parks:
                    print("No parks available to edit.")
                    continue
                print("\nSelect Park to edit:")
                for i, p in enumerate(parks):
                    print(f"{i+1}. {p.get('name')} ({p.get('park_id')})")
                try:
                    idx = int(input("Select (number, 0 to go back): ").strip()) - 1
                except Exception:
                    print("Invalid input.")
                    continue
                if idx == -1:
                    continue
                if idx < 0 or idx >= len(parks):
                    print("Invalid selection.")
                    continue

                park_doc = parks[idx]
                park = Park(**park_doc)

                # Edit loop for selected park
                while True:
                    print(f"\nEditing Park: {park.name} ({park.park_id})")
                    print("1. Edit Name")
                    print("2. Edit Location")
                    print("3. Edit Description")
                    print("4. Edit Max Capacity")
                    print("5. Manage Schedules")
                    print("6. Edit Ticket Price")
                    print("0. Back")
                    sub = input("Select (number, 0 to go back): ").strip()
                    if sub == '0':
                        break
                    if sub == '1':
                        while True:
                            newname = input("New name: ").strip()
                            if not newname:
                                print("Name cannot be empty.")
                                continue
                            try:
                                park.update_name(newname)
                                park.save()
                                AuditLog.log(admin_user.name, "SYSTEM", f"Updated Park name {park.park_id} -> {newname}")
                                print("Name updated.")
                                break
                            except Exception:
                                print("Failed to update name.")
                                break
                    elif sub == '2':
                        while True:
                            newloc = input("New location: ").strip()
                            if not newloc:
                                print("Location cannot be empty.")
                                continue
                            try:
                                park.update_location(newloc)
                                park.save()
                                AuditLog.log(admin_user.name, "SYSTEM", f"Updated Park location {park.park_id}")
                                print("Location updated.")
                                break
                            except Exception:
                                print("Failed to update location.")
                                break
                    elif sub == '3':
                        while True:
                            newdesc = input("New description: ").strip()
                            if newdesc == '':
                                ok = input("Empty description — confirm (y/n)? ").strip().lower()
                                if ok != 'y':
                                    continue
                            try:
                                park.update_description(newdesc)
                                park.save()
                                AuditLog.log(admin_user.name, "SYSTEM", f"Updated Park description {park.park_id}")
                                print("Description updated.")
                                break
                            except Exception:
                                print("Failed to update description.")
                                break
                    elif sub == '4':
                        # Edit park-level max capacity
                        while True:
                            try:
                                newc = int(input("New park max capacity: ").strip())
                                if newc <= 0:
                                    print("Capacity must be a positive integer.")
                                    continue
                                break
                            except Exception:
                                print("Invalid input. Enter a positive integer.")
                        try:
                            park.update_max_capacity(newc)
                            AuditLog.log(admin_user.name, "SYSTEM", f"Updated Park max capacity {park.park_id} -> {newc}")
                            print("Max capacity updated.")
                        except Exception as e:
                            print(f"Failed to update max capacity: {e}")
                        continue

                    elif sub == '5':
                        # Manage schedules for this park
                        while True:
                            print(f"\nSchedules for {park.name}:")
                            for i, s in enumerate(park.schedules):
                                # Show remaining using park-level capacity
                                remaining = park.max_capacity - s.current_occupancy
                                print(f"{i+1}. {s} | Remaining: {remaining}/{park.max_capacity}")
                            print("a. Add schedule")
                            print("b. Back")
                            action = input("Choice: ").strip().lower()
                            if action == 'b':
                                break
                            if action == 'a':
                                while True:
                                    date = input("Date (YYYY-MM-DD): ").strip()
                                    if not date:
                                        print("Date cannot be empty.")
                                        continue
                                    try:
                                        datetime.strptime(date, "%Y-%m-%d")
                                        break
                                    except Exception:
                                        print("Invalid date format. Use YYYY-MM-DD.")
                                try:
                                    park.add_schedule(Schedule(date))
                                    park.save()
                                    AuditLog.log(admin_user.name, "SYSTEM", f"Added schedule {date} to {park.park_id}")
                                    print("Schedule added.")
                                except Exception as e:
                                    print(f"Failed to add schedule: {e}")
                                continue
                            # edit/delete existing schedule
                            try:
                                sidx = int(action) - 1
                            except Exception:
                                print("Invalid input.")
                                continue
                            if sidx < 0 or sidx >= len(park.schedules):
                                print("Invalid selection.")
                                continue
                            sched = park.schedules[sidx]
                            print(f"Selected: {sched}")
                            print("1. Delete schedule")
                            print("0. Back")
                            sub2 = input("Select (number, 0 to go back): ").strip()
                            if sub2 == '0':
                                continue
                            if sub2 == '1':
                                confirm = input(f"Delete schedule {sched.visit_date}? (y/n): ").strip().lower()
                                if confirm == 'y':
                                    try:
                                        park.remove_schedule(sched.visit_date)
                                        park.save()
                                        AuditLog.log(admin_user.name, "SYSTEM", f"Deleted schedule {park.park_id} {sched.visit_date}")
                                        print("Schedule deleted.")
                                    except Exception as e:
                                        print(f"Failed to delete schedule: {e}")
                                else:
                                    print("Canceled.")
                            else:
                                print("Invalid choice.")

                    elif sub == '6':
                        # Edit ticket price
                        while True:
                            try:
                                # Show current price to the admin when prompting
                                current_display = f"${park.ticket_price:.2f}" if park.ticket_price is not None else "NOT SET"
                                newp = input(f"New ticket price (current: {current_display}) : ").strip()
                                if newp == '':
                                    price_val = park.ticket_price
                                    break
                                price_val = float(newp)
                                if price_val < 0:
                                    print("Price must be non-negative.")
                                    continue
                                break
                            except Exception:
                                print("Invalid input. Enter a numeric price or press Enter to keep current.")
                        try:
                            park.ticket_price = price_val
                            park.save()
                            AuditLog.log(admin_user.name, "SYSTEM", f"Updated Park ticket price {park.park_id} -> {price_val}")
                            print("Ticket price updated.")
                        except Exception as e:
                            print(f"Failed to update ticket price: {e}")
                        continue

                    else:
                        print("Invalid selection.")

            elif choice == '3':
                # Delete park
                parks = Database.get_all_parks()
                if not parks:
                    print("No parks available to delete.")
                    continue
                print("\nSelect Park to delete:")
                for i, p in enumerate(parks):
                    print(f"{i+1}. {p.get('name')} ({p.get('park_id')})")
                try:
                    idx = int(input("Select (number, 0 to go back): ").strip()) - 1
                except Exception:
                    print("Invalid input.")
                    continue
                if idx == -1:
                    continue
                if idx < 0 or idx >= len(parks):
                    print("Invalid selection.")
                    continue
                park_doc = parks[idx]
                park = Park(**park_doc)
                confirm = input(f"Confirm delete park {park_doc.get('name')} ({park_doc.get('park_id')})? (y/n): ").strip().lower()
                if confirm == 'y':
                    try:
                        park.delete()
                        AuditLog.log(admin_user.name, "SYSTEM", f"Deleted Park {park_doc.get('park_id')}")
                        print("\nPark deleted.")
                    except Exception:
                        print("\nFailed to delete park.")
                else:
                    print("\nCanceled.")

            elif choice == '4':
                parks = Database.get_all_parks()
                if not parks:
                    print("\nNo parks available.")
                    continue
                print("\n--- All Parks ---")
                for i, p in enumerate(parks):
                    park = Park(**p)
                    print(f"{i+1}. {park.name} ({park.park_id})")
                    print(f"   Location: {park.location}")
                    print(f"   Description: {park.description}")
                    if park.ticket_price is None:
                        print(f"   Ticket price: NOT SET")
                    else:
                        print(f"   Ticket price: ${park.ticket_price:.2f}")
                    if park.schedules:
                        print("   Schedules:")
                        for s in park.schedules:
                            remaining = park.max_capacity - s.current_occupancy
                            print(f"     - {s.visit_date}: Max {park.max_capacity}, Current {s.current_occupancy}, Remaining {remaining}")
                    else:
                        print("   No schedules.")
            else:
                print("Invalid choice.")

    def manage_inventory(self):
        """Manage Merchandise: add, edit, delete, list."""
        while True:
            # Determine admin name from current authentication context for audit logs
            try:
                from services import AuthenticationManager as _AuthCls
            except Exception:
                _AuthCls = AuthenticationManager
            auth = _AuthCls()
            admin_name = getattr(auth.current_user, 'name', 'SYSTEM')
            print("\n--- Manage Merchandise ---")
            print("1. Add Merchandise")
            print("2. Edit Merchandise")
            print("3. Delete Merchandise")
            print("4. List Merchandise")
            print("0. Back")
            choice = input("Select (number, 0 to go back): ").strip()
            if choice == '0':
                return

            if choice == '1':
                # Add new merchandise
                print("\n--- Add Merchandise ---")
                while True:
                    sku = input("SKU: ").strip()
                    if sku:
                        break
                    print("SKU cannot be empty.")
                # Check duplicate
                existing = Database.merch_col.find_one({'sku': sku})
                if existing:
                    print("SKU already exists.")
                    continue
                while True:
                    name = input("Name: ").strip()
                    if name:
                        break
                    print("Name cannot be empty.")
                while True:
                    try:
                        price = float(input("Price: ").strip())
                        if price < 0:
                            print("Price cannot be negative.")
                            continue
                        break
                    except Exception:
                        print("Enter a valid number for price.")
                while True:
                    try:
                        stock = int(input("Stock quantity: ").strip())
                        if stock < 0:
                            print("Stock cannot be negative.")
                            continue
                        break
                    except Exception:
                        print("Enter a valid integer for stock.")
                m = Merchandise(sku, name, price, stock)
                try:
                    m.save()
                    AuditLog.log(admin_name, "SYSTEM", f"Added Merchandise {sku} - {name}")
                    print("Merchandise added.")
                except Exception as e:
                    print(f"Failed to add merchandise: {e}")
                continue

            if choice == '4':
                merch_data = Database.get_all_merchandise()
                if not merch_data:
                    print("No merchandise available.")
                    continue
                print("\n--- All Merchandise ---")
                for i, m in enumerate(merch_data):
                    print(f"{i+1}. {m.get('name')} (SKU: {m.get('sku')}) - Price: {m.get('price')} - Stock: {m.get('stock_quantity')}")
                continue

            if choice == '2':
                merch_data = Database.get_all_merchandise()
                if not merch_data:
                    print("No merchandise available to edit.")
                    continue
                print("\nSelect merchandise to edit:")
                for i, m in enumerate(merch_data):
                    print(f"{i+1}. {m.get('name')} (SKU: {m.get('sku')})")
                try:
                    idx = int(input("Select (number, 0 to go back): ").strip()) - 1
                except Exception:
                    print("Invalid input.")
                    continue
                if idx == -1:
                    continue
                if idx < 0 or idx >= len(merch_data):
                    print("Invalid selection.")
                    continue
                doc = merch_data[idx]
                merch = Merchandise(doc.get('sku'), doc.get('name'), doc.get('price'), doc.get('stock_quantity'))
                # Edit submenu
                while True:
                    print(f"\nEditing Merchandise: {merch.name} (SKU: {merch.sku})")
                    print("1. Edit Name")
                    print("2. Edit Price")
                    print("3. Edit Stock")
                    print("0. Back")
                    sub = input("Select (number, 0 to go back): ").strip()
                    if sub == '0':
                        break
                    if sub == '1':
                        newname = input("New name: ").strip()
                        if not newname:
                            print("Name cannot be empty.")
                            continue
                        merch.name = newname
                        try:
                            merch.save()
                            AuditLog.log(admin_name, "SYSTEM", f"Updated Merchandise name {merch.sku} -> {newname}")
                            print("Name updated.")
                        except Exception as e:
                            print(f"Failed to update name: {e}")
                    elif sub == '2':
                        try:
                            newprice = float(input("New price: ").strip())
                            if newprice < 0:
                                print("Price cannot be negative.")
                                continue
                            merch.price = newprice
                            merch.save()
                            AuditLog.log(admin_name, "SYSTEM", f"Updated Merchandise price {merch.sku} -> {newprice}")
                            print("Price updated.")
                        except Exception:
                            print("Invalid price input.")
                    elif sub == '3':
                        try:
                            newstock = int(input("New stock quantity: ").strip())
                            if newstock < 0:
                                print("Stock cannot be negative.")
                                continue
                            merch.stock_quantity = newstock
                            merch.save()
                            AuditLog.log(admin_name, "SYSTEM", f"Updated Merchandise stock {merch.sku} -> {newstock}")
                            print("Stock updated.")
                        except Exception:
                            print("Invalid stock input.")
                    else:
                        print("Invalid selection.")
                continue

            if choice == '3':
                merch_data = Database.get_all_merchandise()
                if not merch_data:
                    print("No merchandise available to delete.")
                    continue
                print("\nSelect merchandise to delete:")
                for i, m in enumerate(merch_data):
                    print(f"{i+1}. {m.get('name')} (SKU: {m.get('sku')})")
                try:
                    idx = int(input("Select (number, 0 to go back): ").strip()) - 1
                except Exception:
                    print("Invalid input.")
                    continue
                if idx == -1:
                    continue
                if idx < 0 or idx >= len(merch_data):
                    print("Invalid selection.")
                    continue
                doc = merch_data[idx]
                merch = Merchandise(doc.get('sku'), doc.get('name'), doc.get('price'), doc.get('stock_quantity'))
                confirm = input(f"Confirm delete {merch.name} (SKU: {merch.sku})? (y/n): ").strip().lower()
                if confirm == 'y':
                    try:
                        merch.delete()
                        AuditLog.log(admin_name, "SYSTEM", f"Deleted Merchandise {merch.sku}")
                        print("Merchandise deleted.")
                    except Exception as e:
                        print(f"Failed to delete merchandise: {e}")
                else:
                    print("Canceled.")
                continue

            print("Invalid choice.")

    def view_reports(self):
        # Interactive reports menu with multiple breakdowns
        while True:
            print("\n--- ANALYTICS REPORT ---")
            print("1. Summary (total revenue & orders)")
            print("2. Breakdown by Park (tickets)")
            print("3. Breakdown by Date Range")
            print("4. Breakdown by Payment Status")
            print("5. Breakdown by Merchandise Orders")
            print("6. Revenue by Region (customer snapshot)")
            print("7. Visitor Counts by Age Group (unique visitors & orders)")
            print("0. Back")
            choice = input("Select (number, 0 to go back): ").strip()
            if choice == '0' or choice == '':
                return

            orders = Database.get_all_orders()

            if choice == '1':
                total_rev = sum((o.get('total_cost') or 0) for o in orders)
                print("\n-- Summary --")
                print(f"Total Revenue: ${total_rev:.2f}")
                print(f"Total Orders: {len(orders)}")

            elif choice == '2':
                # Sum ticket revenue and counts by park (use line_items metadata)
                park_stats = {}
                for o in orders:
                    for li in o.get('line_items', []):
                        if li.get('item_type') == 'TICKET':
                            meta = li.get('metadata') or {}
                            park = meta.get('park_name') or meta.get('park_id') or li.get('item_name') or 'UNKNOWN'
                            rev = (li.get('unit_price') or 0) * (li.get('quantity') or 1)
                            stats = park_stats.setdefault(park, {'revenue': 0.0, 'tickets': 0})
                            stats['revenue'] += rev
                            stats['tickets'] += (li.get('quantity') or 1)
                if not park_stats:
                    print("\nNo ticket sales found in orders.")
                else:
                    print("\n-- Revenue by Park (tickets) --")
                    for park, s in sorted(park_stats.items(), key=lambda kv: kv[1]['revenue'], reverse=True):
                        print(f"{park}: ${s['revenue']:.2f} across {s['tickets']} ticket(s)")

            elif choice == '3':
                # Date range filter (orders have 'date' as datetime)
                try:
                    start_in = input("Start date (YYYY-MM-DD): ").strip()
                    end_in = input("End date (YYYY-MM-DD): ").strip()
                    start_dt = datetime.strptime(start_in, "%Y-%m-%d")
                    end_dt = datetime.strptime(end_in, "%Y-%m-%d")
                except Exception:
                    print("Invalid date format. Use YYYY-MM-DD.")
                    continue
                # normalize end to end of day
                end_dt = end_dt.replace(hour=23, minute=59, second=59)
                total_rev = 0.0
                count = 0
                for o in orders:
                    od = o.get('date')
                    if od is None:
                        continue
                    try:
                        # if stored as string, try parse
                        if isinstance(od, str):
                            odt = datetime.fromisoformat(od)
                        else:
                            odt = od
                    except Exception:
                        continue
                    if start_dt <= odt <= end_dt:
                        total_rev += (o.get('total_cost') or 0)
                        count += 1
                print(f"\nOrders between {start_in} and {end_in}: {count}")
                print(f"Revenue in range: ${total_rev:.2f}")

            elif choice == '4':
                # Group by payment_status
                status_stats = {}
                for o in orders:
                    status = o.get('payment_status', 'UNKNOWN')
                    s = status_stats.setdefault(status, {'revenue': 0.0, 'orders': 0})
                    s['revenue'] += (o.get('total_cost') or 0)
                    s['orders'] += 1
                print("\n-- By Payment Status --")
                for st, s in status_stats.items():
                    print(f"{st}: {s['orders']} order(s), Revenue: ${s['revenue']:.2f}")

            elif choice == '5':
                # Aggregate merchandise sales across orders (by SKU or item name)
                merch_stats = {}
                for o in orders:
                    for li in o.get('line_items', []):
                        if li.get('item_type') == 'MERCH':
                            meta = li.get('metadata') or {}
                            key = meta.get('sku') or li.get('item_name') or 'UNKNOWN'
                            qty = int(li.get('quantity') or 1)
                            rev = (li.get('unit_price') or 0) * qty
                            entry = merch_stats.setdefault(key, {'name': li.get('item_name'), 'revenue': 0.0, 'quantity': 0})
                            entry['revenue'] += rev
                            entry['quantity'] += qty
                if not merch_stats:
                    print("\nNo merchandise sales found in orders.")
                else:
                    print("\n-- Merchandise Sales --")
                    for sku, s in sorted(merch_stats.items(), key=lambda kv: kv[1]['revenue'], reverse=True):
                        name = s.get('name') or sku
                        print(f"{name} (SKU: {sku}): {s['quantity']} unit(s) sold, Revenue: ${s['revenue']:.2f}")

            elif choice == '6':
                # Revenue aggregated by customer region (lookup current user profile)
                region_stats = {}
                for o in orders:
                    uid = o.get('user_id')
                    user = Database.get_user_by_id(uid) or {}
                    region = user.get('region') or 'UNKNOWN'
                    s = region_stats.setdefault(region, {'revenue': 0.0, 'orders': 0})
                    s['revenue'] += (o.get('total_cost') or 0)
                    s['orders'] += 1
                if not region_stats:
                    print("\nNo customer region data available in user profiles.")
                else:
                    print("\n-- Revenue by Region --")
                    for r, s in sorted(region_stats.items(), key=lambda kv: kv[1]['revenue'], reverse=True):
                        print(f"{r}: {s['orders']} order(s), Revenue: ${s['revenue']:.2f}")

            elif choice == '7':
                # Visitor counts by age group: count unique users and orders per age bucket (lookup current profiles)
                orders_by_age = {}
                unique_users_by_age = {}
                for o in orders:
                    uid = o.get('user_id')
                    user = Database.get_user_by_id(uid) or {}
                    age = user.get('age_group') or 'UNKNOWN'
                    orders_by_age[age] = orders_by_age.get(age, 0) + 1
                    if age not in unique_users_by_age:
                        unique_users_by_age[age] = set()
                    if uid:
                        unique_users_by_age[age].add(uid)
                if not orders_by_age:
                    print("\nNo age-group data available in user profiles.")
                else:
                    print("\n-- Visitor Counts by Age Group --")
                    for age in sorted(orders_by_age.keys()):
                        orders_count = orders_by_age.get(age, 0)
                        unique_count = len(unique_users_by_age.get(age, set()))
                        print(f"{age}: {unique_count} unique visitor(s), {orders_count} order(s)")

            else:
                print("Invalid selection.")

    def view_audit_logs(self):
        logs = AuditLog.get_logs()
        print("\n--- AUDIT LOGS ---")
        for log in logs:
            print(f"[{log['timestamp']}] [{log['category']}] {log['user']}: {log['action']}")

    def resolve_support_tickets(self, admin_user):
        """Interactive flow for viewing and resolving open support tickets."""
        tickets = Database.get_open_support_tickets()
        if not tickets:
            print("\nNo open support tickets.")
            return

        for i, t in enumerate(tickets):
            print(f"{i+1}. {t['description']}")

        while True:
            try:
                idx = int(input("Select (number, 0 to go back): ").strip()) - 1
            except Exception:
                print("Invalid input.")
                continue

            if idx == -1:
                return
            if idx < 0 or idx >= len(tickets):
                print("Invalid selection.")
                continue

            note = input("Note: ").strip()
            st = SupportTicket(**tickets[idx])
            st.resolve(note)
            AuditLog.log(admin_user.name, "SYSTEM", "Resolved Ticket")
            print("Ticket resolved.")
            return

