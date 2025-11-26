"""
Application services and utilities.

This module contains service-layer components used across the
application: auditing, refund policy orchestration, authentication
and a simple admin console facade. Services should coordinate
domain objects and the Database wrapper, avoiding direct UI code.
"""

import uuid
from datetime import datetime, timedelta
from models import Customer, Admin, Audit

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
        Audit.log(entry)

    @staticmethod
    def get_logs():
        return Audit.get_all()

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
                # Use model wrappers to update persistent state
                from models import Ticket, Park
                try:
                    Ticket.set_status(self.ticket.ticket_id, "CANCELLED")
                except Exception:
                    pass

                park_id = getattr(self.ticket, 'park_id', None)
                if park_id:
                    try:
                        Park.decrement_occupancy(park_id, self.ticket.visit_date, 1)
                    except Exception:
                        pass
            except Exception:
                # Fallback: ignore failures
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
        # Check for Admin first to avoid reconstructing Admins as Customers
        user = None
        admin = Admin.load_by_email(email)
        if admin and getattr(admin, 'password', None) == password:
            user = admin
        else:
            cust = Customer.load_by_email(email)
            if cust and getattr(cust, 'password', None) == password:
                user = cust

        if user:
            self.current_user = user
            AuditLog.log(user.name, "USER", "Logged In")
            return user
        return None

    def logout(self):
        if self.current_user:
            AuditLog.log(self.current_user.name, "USER", "Logged Out")
            self.current_user = None

    def register_customer(self, name, email, password):
        if Customer.load_by_email(email):
            return False
        # Generate a sequential customer id in format custXX
        # Count existing customers and add 1 (pad to 2 digits)
        new_num = Customer.count_customers() + 1
        user_id = f"cust{new_num:02d}"
        new_user = Customer(user_id, name, email, password)
        # Use model convenience save method
        new_user.save()
        AuditLog.log(name, "USER", "Registered new account")
        return True

