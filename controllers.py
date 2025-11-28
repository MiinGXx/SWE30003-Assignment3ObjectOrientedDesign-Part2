"""
Controllers for interactive console flows.

This module provides `CustomerConsole` and `AdminConsole`, each exposing
`run(user)` for their respective authenticated sessions. Controllers
handle user interaction and delegate domain operations to models and
services.
"""
from datetime import datetime
from services import AuditLog, AuthenticationManager, RefundRequest
from models import SupportTicket, Park, Schedule, Merchandise, Order, Customer, Ticket, LineItem

class CustomerConsole:
    """Facade for Customer interactive flows — mirrors AdminConsole style.

    Delegates to the provided `cli` instance for concrete flow
    implementations. The console provides a single, encapsulated
    entrypoint for customer interactions, focused on testability and
    modularity. It calls into domain models and services to perform
    persistent operations.
    """

    def run(self, customer):
        """Main loop for an authenticated customer session.

        Presents the customer menu, dispatches to handlers and delegates
        domain operations to models and services.
        """
        menu_actions = {
            '1': lambda customer: self.buy_tickets(customer),
            '2': lambda customer: self.buy_merch(customer),
            '3': lambda customer: self.checkout(customer),
            '4': lambda customer: self.account(customer),
            '5': self.contact_support,
            '6': self.logout
        }

        self._running = True
        while self._running:
            print("\n--- Customer Menu ---")
            print("1. View Parks & Buy Tickets")
            print("2. Browse Merchandise")
            print("3. Checkout Cart")
            print("4. My Account / Refunds")
            print("5. Contact Support")
            print("6. Logout")
            choice = input("Choice: ")
            action = menu_actions.get(choice)
            if action:
                # action may be a bound function that expects customer
                # or one that handles it itself (contact_support/logout)
                try:
                    res = action(customer) if callable(action) and action != self.contact_support and action != self.logout else action(customer)
                except TypeError:
                    # fallback: call without args
                    action()
            else:
                print("Invalid choice.")

    def contact_support(self, customer):
        desc = input("\nIssue: ").strip()
        if not desc:
            print("Error: Description cannot be empty. Returning to Customer Menu.")
            return
        t = SupportTicket(customer.user_id, desc)
        try:
            t.save()
            print("Ticket submitted.")
        except Exception:
            print("Failed to submit ticket. Try again later.")

    def logout(self, customer):
        try:
            from services import AuthenticationManager as _AuthCls
        except Exception:
            _AuthCls = AuthenticationManager
        try:
            _AuthCls().logout()
        except Exception:
            pass
        self._running = False

    def buy_tickets(self, customer):
        parks = Park.get_all()

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
        reserved_in_cart = 0
        for it in customer.cart.items:
            if it.item_type == 'TICKET' and getattr(it.item_obj, 'park_id', None) == selected_park.park_id and it.metadata and it.metadata.get('date') == schedule.visit_date:
                reserved_in_cart += it.quantity

        if not schedule.is_available(qty + reserved_in_cart, selected_park.max_capacity):
            remaining = max(0, selected_park.max_capacity - schedule.current_occupancy - reserved_in_cart)
            print(f"Cannot add {qty} tickets. This park supports up to {selected_park.max_capacity} visitors per date; only {remaining} spot(s) remain for {schedule.visit_date} considering your cart.")
            return

        meta = {'date': schedule.visit_date, 'park_id': selected_park.park_id, 'park_name': selected_park.name}
        price = selected_park.ticket_price
        if price is None:
            print("Cannot add tickets: ticket price for this park is not set. Contact an admin.")
            return
        item = LineItem('TICKET', selected_park, qty, price, meta)
        customer.add_to_cart(item)
        print(f"\nAdded {qty} tickets for {selected_park.name} on {date_in} to cart for checkout.")
        return

    def buy_merch(self, customer):
        merch_list = Merchandise.get_all()

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

                already_in_cart = 0
                for it in customer.cart.items:
                    if it.item_type == 'MERCH' and getattr(it.item_obj, 'sku', None) == getattr(selected_merch, 'sku', None):
                        already_in_cart += it.quantity

                if selected_merch.stock_quantity >= (already_in_cart + qty):
                    item = LineItem('MERCH', selected_merch, qty, selected_merch.price)
                    customer.add_to_cart(item)
                    print("\nItem(s) added to cart for checkout.")
                    return
                else:
                    available = max(0, selected_merch.stock_quantity - already_in_cart)
                    print(f"Low stock. Only {available} more item(s) available to add considering your cart.")
                break

    def checkout(self, customer):
        total = customer.cart.get_total()

        if customer.cart.items:
            print("\n=== Cart Items ===")
            for i, item in enumerate(customer.cart.items):
                item_dict = item.to_dict()
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
        if total == 0:
            return

        if input("Confirm (y/n)? ") == 'y':
            final_line_items = []
            for item in customer.cart.items:
                if item.item_type == 'MERCH':
                    item.item_obj.decrease_stock(item.quantity)
                    final_line_items.append(item.to_dict())
                elif item.item_type == 'TICKET':
                    meta = item.metadata or {}
                    visit_date = meta.get('date')
                    park = None
                    park_id = meta.get('park_id')
                    if park_id:
                        park = Park.load_by_park_id(park_id)
                    if not park:
                        park = item.item_obj
                    db_res = Park.try_book(park.park_id, visit_date, item.quantity)
                    if db_res is False:
                        print(f"Failed to book {item.quantity} tickets for {park.name} on {visit_date}: Full capacity.")
                        return
                    if db_res is None:
                        print("Schedule not found or concurrent update occured. Cannot complete checkout.")
                        return
                    ticket_ids = []
                    for _ in range(item.quantity):
                        tid, t_obj = Ticket.create(customer.user_id, park.park_id, park.name, visit_date, item.unit_price)
                        ticket_ids.append(tid)
                        customer.tickets.append(t_obj)
                    item_dict = item.to_dict()
                    item_dict['metadata'] = {"date": visit_date, "ticket_ids": ticket_ids}
                    final_line_items.append(item_dict)

            order = Order(customer.user_id, final_line_items, total)
            order.save()
            AuditLog.log(customer.name, "ORDER", f"Placed order ${total}")
            customer.clear_cart()
            print("\nCheckout Complete!")
        else:
            print("Transaction cancelled or Insufficient Funds.")

    def account(self, customer):
        print("\n--- My Account ---")
        print("1. Manage Bookings")
        print("2. View Tickets")
        print("3. Edit Demographics / Profile")
        print("0. Back")
        choice = input("Select (number, 0 to go back): ").strip()
        if choice == '1':
            self.manage_bookings(customer)
        elif choice == '2':
            self.view_tickets(customer)
        elif choice == '3':
            self.edit_demographics(customer)
        else:
            return

    def edit_demographics(self, customer: Customer):
        print("\n--- Edit Demographics ---")
        print("Press Enter to keep current value.")
        cur_age = getattr(customer, 'age_group', None) or ''
        cur_gender = getattr(customer, 'gender', None) or ''
        cur_region = getattr(customer, 'region', None) or ''
        cur_type = getattr(customer, 'visitor_type', None) or ''
        cur_opt = getattr(customer, 'marketing_opt_in', False)

        age_groups = ["<18", "18-24", "25-34", "35-44", "45-54", "55+"]
        print("\nSelect Age Group (press Enter to keep current):")
        for i, ag in enumerate(age_groups, start=1):
            marker = " (current)" if ag == cur_age else ""
            print(f"{i}. {ag}{marker}")
        print("0. Keep current / Skip")
        age = cur_age
        while True:
            sel = input("Select age group (number, 0 to keep current): ").strip()
            if sel == '':
                age = cur_age
                break
            if sel == '0':
                age = cur_age
                break
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(age_groups):
                    age = age_groups[idx]
                    break
            except Exception:
                pass
            print("Invalid selection. Choose a number from the list or 0 to keep current.")

        gender = cur_gender
        while True:
            g = input(f"Gender (Male/Female) (current: {cur_gender}) (press Enter to keep current): ").strip()
            if g == '':
                gender = cur_gender
                break
            if g.lower() in ('male', 'female'):
                gender = 'Male' if g.lower() == 'male' else 'Female'
                break
            print("Please enter only 'Male' or 'Female', or press Enter to keep current.")

        region = input(f"Region (current: {cur_region}) (press Enter to keep current): ").strip()
        if region == '':
            region = cur_region

        vtype = cur_type
        while True:
            vt = input(f"Visitor type (local/domestic/tourist) (current: {cur_type}) (press Enter to keep current): ").strip().lower()
            if vt == '':
                vtype = cur_type
                break
            if vt in ('local', 'domestic', 'tourist'):
                vtype = vt
                break
            print("Invalid input. Enter one of: local, domestic, tourist (or press Enter to keep current).")

        print("\nMarketing opt-in allows us to email you promotional offers, park updates, and event notifications. You can change this later in My Account.")
        opt_in = None
        while True:
            ans = input(f"Marketing opt-in? (y/n) (current: {'y' if cur_opt else 'n'}) (press Enter to keep current): ").strip().lower()
            if ans == '':
                opt_in = cur_opt
                break
            if ans in ('y', 'n'):
                opt_in = True if ans == 'y' else False
                break
            print("Please enter 'y' or 'n', or press Enter to keep current.")

        profile_update = {}
        if age != cur_age and age != '':
            customer.age_group = age
            profile_update['age_group'] = age
        if gender != cur_gender and gender != '':
            customer.gender = gender
            profile_update['gender'] = gender
        if region != cur_region and region != '':
            customer.region = region
            profile_update['region'] = region
        if vtype != cur_type and vtype != '':
            customer.visitor_type = vtype
            profile_update['visitor_type'] = vtype
        if opt_in is not None and opt_in != cur_opt:
            customer.marketing_opt_in = bool(opt_in)
            profile_update['marketing_opt_in'] = bool(opt_in)

        if profile_update:
            try:
                customer.update_profile(profile_update)
                print("Profile updated.")
            except Exception as e:
                print(f"Failed to update profile: {e}")
        else:
            print("No changes made.")

    def view_tickets(self, customer: Customer):
        tickets = Ticket.find_by_owner(customer.user_id)
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
        ticket_id = tdoc.get('ticket_id')
        if ticket_id:
            print("\nQR Code (ticket id):")
            self._display_qr_in_terminal(str(ticket_id))
        else:
            print("No ticket id available to render QR.")

    def _display_qr_in_terminal(self, data: str):
        try:
            import segno
        except Exception:
            print("(Install 'segno' to see QR codes in terminal: `pip install segno`)")
            print(data)
            return
        try:
            qr = segno.make(data)
            qr.terminal(compact=True)
        except Exception as e:
            print("Failed to render QR in terminal:", e)
            print(data)

    def manage_bookings(self, customer):
        tickets = Ticket.find_by_owner(customer.user_id, status='CONFIRMED')
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
        while True:
            print(f"\nBooking: [{ticket_obj.ticket_id}] {ticket_obj.park_name} on {ticket_obj.visit_date}")
            print("1. Reschedule")
            print("2. Cancel / Request Refund")
            print("0. Back")
            choice = input("Select (number, 0 to go back): ").strip()
            if choice == '0':
                return
            elif choice == '1':
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
                park_obj = Park.load_by_park_id(park_id)
                if not park_obj:
                    print("Park not found in system. Cannot reschedule.")
                    return
                schedule_dates = [s.visit_date for s in park_obj.schedules]
                if new_date not in schedule_dates:
                    try:
                        park_obj.add_schedule(Schedule(new_date))
                        park_obj.save_schedules()
                        AuditLog.log(customer.name, "SYSTEM", f"Auto-created schedule {new_date} for {park_id}")
                    except Exception:
                        pass
                booked = Park.try_book(park_id, new_date, 1)
                if booked is False:
                    print("Requested date is full. Cannot reschedule.")
                    continue
                if booked is None:
                    print("Requested date not available. Cannot reschedule.")
                    continue
                try:
                    Park.decrement_occupancy(park_id, ticket_obj.visit_date, 1)
                except Exception:
                    pass
                try:
                    ok = Ticket.update_visit_date(ticket_obj.ticket_id, new_date)
                    if not ok:
                        print("Failed to update booking.")
                        return
                except Exception as e:
                    print(f"Failed to update booking: {e}")
                    return
                for t in customer.tickets:
                    if getattr(t, 'ticket_id', None) == ticket_obj.ticket_id:
                        t.visit_date = new_date
                print("Reschedule successful.")
                AuditLog.log(customer.name, "BOOKING", f"Rescheduled {ticket_obj.ticket_id} to {new_date}")
                return
            elif choice == '2':
                req = RefundRequest(ticket_obj, customer)
                ok = req.process_refund()
                if ok:
                    print("Refund processed.")
                    AuditLog.log(customer.name, "BOOKING", f"Refunded {ticket_obj.ticket_id}")
                    return
                print("Refund denied by policy (less than 24 hours before visit) or failed.")
                confirm = input("Do you still want to cancel the booking without refund? (y/n): ").strip().lower()
                if confirm != 'y':
                    print("Cancellation aborted. No changes made.")
                    return
                try:
                    Ticket.set_status(ticket_obj.ticket_id, "CANCELLED")
                except Exception:
                    pass
                try:
                    if ticket_obj.park_id:
                        Park.decrement_occupancy(ticket_obj.park_id, ticket_obj.visit_date, 1)
                except Exception:
                    pass
                try:
                    customer.tickets = [t for t in customer.tickets if getattr(t, 'ticket_id', None) != ticket_obj.ticket_id]
                except Exception:
                    pass
                AuditLog.log(customer.name, "BOOKING", f"Cancelled without refund {ticket_obj.ticket_id}")
                print("Booking cancelled. No refund will be issued.")
                return
            else:
                print("Invalid choice.")


class AdminConsole:
    """Admin console for interactive administrator flows.

    Provides management menus for parks, merchandise, reporting,
    audit logs and support ticket resolution. Delegates domain work
    to models and services.
    """

    def run(self, admin_user):
        """Main loop for an authenticated admin session.

        Presents the top-level admin menu and dispatches to admin helpers
        such as `manage_park`, `manage_inventory`, and reporting utilities.
        """
        # Use AuthenticationManager singleton for logout and auditing context
        try:
            from services import AuthenticationManager as _AuthCls
        except Exception:
            _AuthCls = AuthenticationManager
        auth = _AuthCls()

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
                self.manage_park(admin_user)
            elif choice == '2':
                self.manage_inventory()
            elif choice == '3':
                self.view_reports()
            elif choice == '4':
                self.view_audit_logs()
            elif choice == '5':
                self.resolve_support_tickets(admin_user)
            elif choice == '6':
                try:
                    auth.logout()
                except Exception:
                    pass
                return
            else:
                print("Invalid choice.")

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
                parks = Park.get_all()
                if not parks:
                    print("No parks available to edit.")
                    continue
                print("\nSelect Park to edit:")
                for i, p in enumerate(parks):
                    print(f"{i+1}. {p.name} ({p.park_id})")
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

                park = parks[idx]

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
                parks = Park.get_all()
                if not parks:
                    print("No parks available to delete.")
                    continue
                print("\nSelect Park to delete:")
                for i, p in enumerate(parks):
                    print(f"{i+1}. {p.name} ({p.park_id})")
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
                park = parks[idx]
                confirm = input(f"Confirm delete park {park.name} ({park.park_id})? (y/n): ").strip().lower()
                if confirm == 'y':
                    try:
                        park.delete()
                        AuditLog.log(admin_user.name, "SYSTEM", f"Deleted Park {park.park_id}")
                        print("\nPark deleted.")
                    except Exception:
                        print("\nFailed to delete park.")
                else:
                    print("\nCanceled.")

            elif choice == '4':
                parks = Park.get_all()
                if not parks:
                    print("\nNo parks available.")
                    continue
                print("\n--- All Parks ---")
                for i, park in enumerate(parks):
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
                existing = Merchandise.load_by_sku(sku)
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
                merch_list = Merchandise.get_all()
                if not merch_list:
                    print("No merchandise available.")
                    continue
                print("\n--- All Merchandise ---")
                for i, m in enumerate(merch_list):
                    print(f"{i+1}. {m.name} (SKU: {m.sku}) - Price: {m.price} - Stock: {m.stock_quantity}")
                continue

            if choice == '2':
                merch_list = Merchandise.get_all()
                if not merch_list:
                    print("No merchandise available to edit.")
                    continue
                print("\nSelect merchandise to edit:")
                for i, m in enumerate(merch_list):
                    print(f"{i+1}. {m.name} (SKU: {m.sku})")
                try:
                    idx = int(input("Select (number, 0 to go back): ").strip()) - 1
                except Exception:
                    print("Invalid input.")
                    continue
                if idx == -1:
                    continue
                if idx < 0 or idx >= len(merch_list):
                    print("Invalid selection.")
                    continue
                merch = merch_list[idx]
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
                merch_list = Merchandise.get_all()
                if not merch_list:
                    print("No merchandise available to delete.")
                    continue
                print("\nSelect merchandise to delete:")
                for i, m in enumerate(merch_list):
                    print(f"{i+1}. {m.name} (SKU: {m.sku})")
                try:
                    idx = int(input("Select (number, 0 to go back): ").strip()) - 1
                except Exception:
                    print("Invalid input.")
                    continue
                if idx == -1:
                    continue
                if idx < 0 or idx >= len(merch_list):
                    print("Invalid selection.")
                    continue
                merch = merch_list[idx]
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
            # Load orders once and compute opt-in/unknown summary for analytics
            orders = Order.get_all()
            unique_user_ids = set([o.get('user_id') for o in orders if o.get('user_id')])
            opted_in_count = 0
            unknown_count = 0
            for uid in unique_user_ids:
                try:
                    u = Customer.load_by_id(uid)
                except Exception:
                    u = None
                if u and getattr(u, 'marketing_opt_in', False):
                    opted_in_count += 1
                else:
                    unknown_count += 1

            print("\n--- ANALYTICS REPORT ---")
            print("(Note: Demographics shown only for customers who opted-in to marketing; opted-out users are labelled 'UNKNOWN' in demographic breakdowns.)")
            print(f"Opted-in customers (present in orders): {opted_in_count} | Unknown / opted-out: {unknown_count}")
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
                    try:
                        user = Customer.load_by_id(uid)
                    except Exception:
                        user = None
                    # Only use real demographics when user opted in; otherwise treat as UNKNOWN
                    if user and getattr(user, 'marketing_opt_in', False):
                        region = getattr(user, 'region', None) or 'UNKNOWN'
                    else:
                        region = 'UNKNOWN'
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
                    try:
                        user = Customer.load_by_id(uid)
                    except Exception:
                        user = None
                    # Only reveal age group when user opted in; otherwise label UNKNOWN
                    if user and getattr(user, 'marketing_opt_in', False):
                        age = getattr(user, 'age_group', None) or 'UNKNOWN'
                    else:
                        age = 'UNKNOWN'
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
        tickets = SupportTicket.get_open()
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
