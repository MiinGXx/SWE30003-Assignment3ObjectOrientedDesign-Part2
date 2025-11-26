"""
CLI entrypoint and interactive flows for the State Park System.

This module presents a console-based user interface for customers
and administrators to interact with the park booking system. It
orchestrates flows, handles input validation and delegates domain
operations to `models`, `services` and `database` wrappers.

The module aims to keep UI logic here while domain behavior and
persistence live in their respective modules.
"""

import sys
import uuid
from datetime import datetime
from database import Database
from models import Park, Schedule, LineItem, Order, Ticket, SupportTicket, Customer, Admin
from services import AuthenticationManager, AdminConsole, AuditLog, RefundRequest

# Initialize DB (Seed if empty)
Database.seed_data()

class CLI:
    """Interactive command-line interface controller.

    Presents menus to users and routes input to domain/service operations.
    All I/O logic lives here to keep domain models pure and testable.
    """

    def __init__(self):
        # Authentication manager (singleton-like facade)
        self.auth = AuthenticationManager()
        # Admin console facade used for admin operations
        self.admin_console = AdminConsole()

    def main_menu(self):
        """Show the top-level menu and route to login/register or exit.

        This loop runs until the process terminates. Each choice is
        delegated to a smaller flow function for clarity and testability.
        """
        while True:
            print("\n=== STATE PARK SYSTEM ===")
            print("1. Login")
            print("2. Register")
            print("3. Exit")
            choice = input("Choice: ")

            if choice == '1':
                self.login_screen()
            elif choice == '2':
                self.register_screen()
            elif choice == '3':
                sys.exit()

    def register_screen(self):
        """Prompt for and register a new customer account.

        Delegates creation to the `AuthenticationManager` and reports
        success or failure to the user.
        """
        print("\n--- Register ---")
        name = input("Name: ")
        email = input("Email: ")
        pw = input("Password: ")
        if self.auth.register_customer(name, email, pw):
            print("Success! Please login.")
        else:
            print("Email already exists.")

    def login_screen(self):
        """Prompt for user credentials and route to role-specific menu.

        Successful logins are logged to the audit trail.
        """
        print("\n--- Login ---")
        email = input("Email: ")
        pw = input("Password: ")
        user = self.auth.login(email, pw)

        if user:
            print(f"\nWelcome, {user.name}!")
            if isinstance(user, Admin):
                self.admin_menu(user)
            elif isinstance(user, Customer):
                self.customer_menu(user)
        else:
            print("Invalid credentials.")

    # ==========================
    # CUSTOMER FLOWS
    # ==========================
    def customer_menu(self, customer: Customer):
        """Display customer options and dispatch to their flows.

        This loop is specific to the authenticated customer's session.
        """
        while True:
            print("\n--- Customer Menu ---")
            print("1. View Parks & Buy Tickets")
            print("2. Browse Merchandise")
            print("3. Checkout Cart")
            print("4. My Account / Refunds")
            print("5. Contact Support")
            print("6. Logout")
            choice = input("Choice: ")

            if choice == '1':
                self.flow_buy_tickets(customer)
            elif choice == '2':
                self.flow_buy_merch(customer)
            elif choice == '3':
                self.flow_checkout(customer)
            elif choice == '4':
                self.flow_account(customer)
            elif choice == '5':
                desc = input("\nIssue: ").strip()
                # Validate description isn't empty; return to customer menu if it is
                if not desc:
                    print("Error: Description cannot be empty. Returning to Customer Menu.")
                    continue
                t = SupportTicket(customer.user_id, desc)
                Database.add_support_ticket(t.to_dict())
                print("Ticket submitted.")
            elif choice == '6':
                self.auth.logout()
                break

    def flow_buy_tickets(self, customer):
        parks_data = Database.get_all_parks()
        # Convert dicts to Objects
        parks = [Park(**p) for p in parks_data]

        print("\nSelect Park:")
        for i, p in enumerate(parks):
            ticket_price = p.ticket_price
            print(f"{i+1}. {p.name}")
            print(f"   Location: {p.location}")
            print(f"   Description: {p.description}")
            print(f"   Max capacity: {p.max_capacity}")
            if ticket_price is None:
                print(f"   Ticket price: NOT SET (contact admin)")
            else:
                print(f"   Ticket price: ${ticket_price:.2f}")

        print("\n0. Back")
        sel = input("Select (number, 0 to go back): ").strip()
        if sel == '0':
            return
        try:
            p_idx = int(sel) - 1
            selected_park = parks[p_idx]
        except Exception:
            print("Invalid park selection.")
            return

        # Prompt for a visit date (free input) and validate it's a future date
        while True:
            date_in = input("Visit Date (YYYY-MM-DD): ").strip()
            try:
                visit_dt = datetime.strptime(date_in, "%Y-%m-%d")
                if visit_dt.date() <= datetime.now().date():
                    print("Please choose a date after today.")
                    continue
                break
            except Exception:
                print("Invalid date format. Use YYYY-MM-DD.")

        # Check if schedule exists for that date
        schedule = None
        for s in selected_park.schedules:
            if s.visit_date == date_in:
                schedule = s
                break

        # If schedule missing, create it using park-level capacity (silently)
        if schedule is None:
            schedule = Schedule(date_in)
            selected_park.add_schedule(schedule)
            # Persist new schedule immediately (no user-facing messages)
            selected_park.save_schedules()

        # Ask for ticket quantity
        while True:
            try:
                qty = int(input("Quantity: "))
                if qty <= 0:
                    print("Please enter a positive integer for quantity.")
                    continue
                break
            except Exception:
                print("Invalid input. Enter a number for quantity.")

        # Check availability and add to cart (defer booking/payment to checkout)
        # Consider existing ticket reservations in cart for same park/date
        reserved_in_cart = 0
        for it in customer.cart.items:
            if it.item_type == 'TICKET' and it.item_obj.park_id == selected_park.park_id and it.metadata and it.metadata.get('date') == schedule.visit_date:
                reserved_in_cart += it.quantity

        if not schedule.is_available(qty + reserved_in_cart, selected_park.max_capacity):
            # Calculate remaining seats considering current occupancy and items already reserved in this customer's cart
            remaining = max(0, selected_park.max_capacity - schedule.current_occupancy - reserved_in_cart)
            print(f"Cannot add {qty} tickets. This park supports up to {selected_park.max_capacity} visitors per date; only {remaining} spot(s) remain for {schedule.visit_date} considering your cart.")
            return

        # Persistable metadata (avoid storing full objects)
        meta = {'date': schedule.visit_date, 'park_id': selected_park.park_id, 'park_name': selected_park.name}
        price = selected_park.ticket_price
        if price is None:
            print("Cannot add tickets: ticket price for this park is not set. Contact an admin.")
            return
        item = LineItem('TICKET', selected_park, qty, price, meta)
        customer.add_to_cart(item)
        print(f"\nAdded {qty} tickets for {selected_park.name} on {date_in} to cart for checkout.")
        # Return to customer menu after adding to cart
        return

    def flow_buy_merch(self, customer):
        merch_data = Database.get_all_merchandise()
        # Convert to objects
        merch_list = [from_merch_dict(m) for m in merch_data] # Helper below

        if not merch_list:
            print("No merchandise available.")
            return

        while True:
            print("\nMerchandise:")
            for i, m in enumerate(merch_list):
                print(f"{i+1}. {m}")
            print("\n0. Back")

            try:
                sel = input("Select (number, 0 to go back): ").strip()
                if sel == '0':
                    return
                idx = int(sel) - 1
                if idx < 0 or idx >= len(merch_list):
                    print("Invalid selection.")
                    continue
                selected_merch = merch_list[idx]
            except Exception:
                print("Invalid input.")
                continue

            # Prompt for quantity with option to go back
            while True:
                qty_in = input(f"Quantity (or 'b' to go back): ").strip().lower()
                if qty_in == 'b':
                    break
                try:
                    qty = int(qty_in)
                    if qty <= 0:
                        print("Enter a positive quantity.")
                        continue
                except Exception:
                    print("Invalid input. Enter a number or 'b' to go back.")
                    continue

                # Consider existing same-SKU items in cart to avoid overselling
                already_in_cart = 0
                for it in customer.cart.items:
                    if it.item_type == 'MERCH' and getattr(it.item_obj, 'sku', None) == getattr(selected_merch, 'sku', None):
                        already_in_cart += it.quantity

                if selected_merch.stock_quantity >= (already_in_cart + qty):
                    item = LineItem('MERCH', selected_merch, qty, selected_merch.price)
                    customer.add_to_cart(item)
                    print("\nItem(s) added to cart for checkout.")
                    # Return to customer menu after adding to cart
                    return
                else:
                    available = max(0, selected_merch.stock_quantity - already_in_cart)
                    print(f"Low stock. Only {available} more item(s) available to add considering your cart.")
                # After processing quantity, return to merch list
                break

    def flow_checkout(self, customer):
        total = customer.cart.get_total()

        # List cart items with qty and price
        if customer.cart.items:
            print("\n=== Cart Items ===")
            for i, item in enumerate(customer.cart.items):
                item_dict = item.to_dict()
                # For ticket items, show "Ticket - <Park Name>" instead of just the park name
                if getattr(item, 'item_type', '') == 'TICKET':
                    park_name = getattr(item.item_obj, 'name', 'Park')
                    name = f"Ticket - {park_name}"
                else:
                    name = item_dict.get('item_name') or getattr(item.item_obj, 'name', 'Item')
                qty = item_dict.get('quantity', item.quantity)
                unit = item_dict.get('unit_price', item.unit_price)
                line_total = qty * unit
                print(f"{i+1}. {name} - Qty: {qty} @ ${unit:.2f} = ${line_total:.2f}")
        else:
            print("\nCart is empty.")

        print(f"\nCart Total: ${total:.2f}")
        if total == 0: return

        if input("Confirm (y/n)? ") == 'y':
            
            # Commit Inventory/Capacity Changes to DB
            final_line_items = []
            for item in customer.cart.items:
                if item.item_type == 'MERCH':
                    item.item_obj.decrease_stock(item.quantity) # Updates DB internally
                    final_line_items.append(item.to_dict())
                
                elif item.item_type == 'TICKET':
                    # Atomic booking for tickets and persistent ticket creation
                    meta = item.metadata or {}
                    visit_date = meta.get('date')
                    # reconstruct park from metadata (persisted carts store park_id)
                    park = None
                    park_id = meta.get('park_id')
                    if park_id:
                        park_doc = Database.parks_col.find_one({'park_id': park_id})
                        if park_doc:
                            park = Park(**park_doc)
                    if not park:
                        # fallback to in-memory object if available
                        park = item.item_obj
                    # Try atomic booking
                    db_res = Database.atomic_book_spots(park.park_id, visit_date, item.quantity)
                    if db_res is False:
                        print(f"Failed to book {item.quantity} tickets for {park.name} on {visit_date}: Full capacity.")
                        return
                    if db_res is None:
                        print("Schedule not found or concurrent update occured. Cannot complete checkout.")
                        return

                    # Create persistent tickets and attach ids
                    ticket_ids = []
                    for _ in range(item.quantity):
                        tid, tdoc = Database.create_ticket(customer.user_id, park.park_id, park.name, visit_date, item.unit_price)
                        ticket_ids.append(tid)
                        customer.tickets.append(Ticket(customer.user_id, park.name, visit_date, item.unit_price, ticket_id=tid, park_id=park.park_id))

                    # Replace metadata ticket ids before saving
                    item_dict = item.to_dict()
                    item_dict['metadata'] = {"date": visit_date, "ticket_ids": ticket_ids}
                    final_line_items.append(item_dict)

            # Save Order
            order = Order(customer.user_id, final_line_items, total)
            Database.add_order(order.to_dict())
            AuditLog.log(customer.name, "ORDER", f"Placed order ${total}")
            
            customer.clear_cart()
            print("\nCheckout Complete!")
        else:
            print("Transaction cancelled or Insufficient Funds.")

    def flow_account(self, customer):
        print("\n--- My Account ---")
        print("1. Manage Bookings")
        print("2. View Tickets")
        print("0. Back")
        choice = input("Select (number, 0 to go back): ").strip()
        if choice == '1':
            self.manage_bookings(customer)
        elif choice == '2':
            self.view_tickets(customer)
        else:
            return

    def view_tickets(self, customer: Customer):
        """List all tickets for the customer and display details + QR code in terminal."""
        try:
            tickets = list(Database.reservations_col.find({'owner_id': customer.user_id}))
        except Exception:
            tickets = []

        if not tickets:
            print("\nYou have no tickets.")
            return

        print("\n--- Your Tickets ---")
        for i, t in enumerate(tickets):
            print(f"{i+1}. [{t.get('ticket_id')}] {t.get('park_name')} on {t.get('visit_date')} (Status: {t.get('status')})")

        try:
            sel = int(input("Select (number, 0 to go back): ").strip()) - 1
        except Exception:
            print("Invalid input.")
            return

        if sel == -1:
            return
        if sel < 0 or sel >= len(tickets):
            print("Invalid selection.")
            return

        tdoc = tickets[sel]
        print("\n--- Ticket Details ---")
        print(f"Ticket ID : {tdoc.get('ticket_id')}")
        print(f"Park      : {tdoc.get('park_name')}")
        print(f"Visit Date: {tdoc.get('visit_date')}")
        print(f"Price     : ${tdoc.get('price'):.2f}")
        print(f"Status    : {tdoc.get('status')}")

        # Display QR code for ticket_id in terminal
        ticket_id = tdoc.get('ticket_id')
        if ticket_id:
            print("\nQR Code (ticket id):")
            self._display_qr_in_terminal(str(ticket_id))
        else:
            print("No ticket id available to render QR.")

    def _display_qr_in_terminal(self, data: str):
        """Try to render a QR in-terminal using `segno`. If not available, print a fallback."""
        try:
            import segno
        except Exception:
            # segno not installed — fallback
            print("(Install 'segno' to see QR codes in terminal: `pip install segno`)")
            print(data)
            return

        try:
            qr = segno.make(data)
            # compact terminal rendering; segno prints to stdout
            qr.terminal(compact=True)
        except Exception as e:
            print("Failed to render QR in terminal:", e)
            print(data)

    def manage_bookings(self, customer):
        """Customer booking management: list upcoming bookings, reschedule, cancel/refund."""
        # Fetch confirmed tickets for this user from DB
        try:
            tickets = list(Database.reservations_col.find({'owner_id': customer.user_id, 'status': 'CONFIRMED'}))
        except Exception:
            tickets = []

        if not tickets:
            print("\nNo upcoming bookings found.")
            return

        print("\n--- Your Bookings ---")
        for i, t in enumerate(tickets):
            print(f"{i+1}. [{t.get('ticket_id')}] {t.get('park_name')} on {t.get('visit_date')}")

        try:
            sel = int(input("Select (number, 0 to go back): ")) - 1
        except Exception:
            print("Invalid input.")
            return

        if sel == -1:
            return
        if sel < 0 or sel >= len(tickets):
            print("Invalid selection.")
            return

        ticket_doc = tickets[sel]
        ticket_obj = Ticket(ticket_doc.get('owner_id'), ticket_doc.get('park_name'), ticket_doc.get('visit_date'), ticket_doc.get('price'), ticket_id=ticket_doc.get('ticket_id'), status=ticket_doc.get('status'), park_id=ticket_doc.get('park_id'))

        # Submenu for selected booking
        while True:
            print(f"\nBooking: [{ticket_obj.ticket_id}] {ticket_obj.park_name} on {ticket_obj.visit_date}")
            print("1. Reschedule")
            print("2. Cancel / Request Refund")
            print("0. Back")
            choice = input("Select (number, 0 to go back): ").strip()
            if choice == '0':
                return
            elif choice == '1':
                # Reschedule flow: ask for new date and attempt atomic booking on new date
                new_date = input("New visit date (YYYY-MM-DD): ").strip()
                try:
                    nd = datetime.strptime(new_date, "%Y-%m-%d")
                    if nd.date() <= datetime.now().date():
                        print("Please choose a future date.")
                        continue
                except Exception:
                    print("Invalid date format.")
                    continue

                park_id = ticket_obj.park_id
                if not park_id:
                    print("Cannot determine park for this booking. Aborting reschedule.")
                    return

                # Check schedule exists; if missing, silently add new schedule and persist
                park_doc = Database.parks_col.find_one({'park_id': park_id})
                if not park_doc:
                    print("Park not found in system. Cannot reschedule.")
                    return

                park_obj = Park(**park_doc)
                schedules = park_doc.get('schedules', []) or []
                schedule_dates = [s.get('visit_date') for s in schedules]
                if new_date not in schedule_dates:
                    try:
                        # silently create and persist the new schedule
                        park_obj.add_schedule(Schedule(new_date))
                        park_obj.save_schedules()
                        AuditLog.log(customer.name, "SYSTEM", f"Auto-created schedule {new_date} for {park_id}")
                    except Exception:
                        # ignore failures here and proceed to booking attempt which will fail cleanly
                        pass

                # Attempt atomic booking on new date (single ticket)
                booked = Database.atomic_book_spots(park_id, new_date, 1)
                if booked is False:
                    print("Requested date is full. Cannot reschedule.")
                    continue
                if booked is None:
                    print("Requested date not available. Cannot reschedule.")
                    continue

                # Decrement old occupancy
                try:
                    Database.decrement_schedule_occupancy(park_id, ticket_obj.visit_date, 1)
                except Exception:
                    pass

                # Update ticket visit_date in DB
                try:
                    Database.reservations_col.update_one({'ticket_id': ticket_obj.ticket_id}, {'$set': {'visit_date': new_date}})
                except Exception as e:
                    print(f"Failed to update booking: {e}")
                    return

                # Update in-memory customer tickets if present
                for t in customer.tickets:
                    if getattr(t, 'ticket_id', None) == ticket_obj.ticket_id:
                        t.visit_date = new_date
                print("Reschedule successful.")
                AuditLog.log(customer.name, "BOOKING", f"Rescheduled {ticket_obj.ticket_id} to {new_date}")
                return

            elif choice == '2':
                # Cancel / Refund
                req = RefundRequest(ticket_obj, customer)
                ok = req.process_refund()
                if ok:
                    print("Refund processed.")
                    AuditLog.log(customer.name, "BOOKING", f"Refunded {ticket_obj.ticket_id}")
                    return

                # Refund was denied (policy). Prompt user to confirm cancellation without refund.
                print("Refund denied by policy (less than 24 hours before visit) or failed.")
                confirm = input("Do you still want to cancel the booking without refund? (y/n): ").strip().lower()
                if confirm != 'y':
                    print("Cancellation aborted. No changes made.")
                    return

                # Proceed to cancel without refund: update ticket status and decrement occupancy
                try:
                    Database.update_ticket_status(ticket_obj.ticket_id, "CANCELLED")
                except Exception:
                    pass
                try:
                    if ticket_obj.park_id:
                        Database.decrement_schedule_occupancy(ticket_obj.park_id, ticket_obj.visit_date, 1)
                except Exception:
                    pass

                # Remove from customer's in-memory tickets if present
                try:
                    customer.tickets = [t for t in customer.tickets if getattr(t, 'ticket_id', None) != ticket_obj.ticket_id]
                except Exception:
                    pass

                AuditLog.log(customer.name, "BOOKING", f"Cancelled without refund {ticket_obj.ticket_id}")
                print("Booking cancelled. No refund will be issued.")
                return
            else:
                print("Invalid choice.")

    # ==========================
    # ADMIN FLOWS
    # ==========================
    def admin_menu(self, admin: Admin):
        while True:
            print("\n--- Admin ---")
            print("1. Manage Park")
            print("2. Manage Merchandise")
            print("3. Reports")
            print("4. Audit Logs")
            print("5. Resolve Support")
            print("6. Logout")
            choice = input("Choice: ")

            if choice == '1':
                self.admin_console.manage_park(admin)
            elif choice == '2':
                self.admin_console.manage_inventory()
            elif choice == '3':
                self.admin_console.view_reports()
            elif choice == '4':
                self.admin_console.view_audit_logs()
            elif choice == '5':
                self.admin_console.resolve_support_tickets(admin)
            elif choice == '6':
                self.auth.logout()
                break

# Helper to reconstruct Merch objects from DB Dicts
def from_merch_dict(d):
    return AdminConsole_Merch_Helper(d['sku'], d['name'], d['price'], d['stock_quantity'])

# Small workaround class to reuse Merchandise logic in main without circular imports or strict typing issues
from models import Merchandise as AdminConsole_Merch_Helper

if __name__ == "__main__":
    app = CLI()
    app.main_menu()