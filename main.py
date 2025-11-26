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
from models import Park, Schedule, LineItem, Order, Ticket, SupportTicket, Customer, Admin, Merchandise
from services import AuthenticationManager, AuditLog, RefundRequest
from controllers import CustomerConsole, AdminConsole

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
        # Customer console facade (delegates to existing CLI flow implementations)
        self.customer_console = CustomerConsole(self, auth=self.auth, audit_log=AuditLog(), admin_console=self.admin_console)

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
            # Offer optional demographic/profile capture
            do_demo = input("Would you like to fill optional demographics now? (y/n): ").strip().lower()
            if do_demo == 'y':
                # Age group selection from predefined buckets
                age_groups = ["<18", "18-24", "25-34", "35-44", "45-54", "55+"]
                print("\nSelect Age Group:")
                for i, ag in enumerate(age_groups, start=1):
                    print(f"{i}. {ag}")
                print("0. Skip")
                age = ''
                while True:
                    sel = input("Select age group (number, 0 to skip): ").strip()
                    if sel == '' or sel == '0':
                        break
                    try:
                        idx = int(sel) - 1
                        if 0 <= idx < len(age_groups):
                            age = age_groups[idx]
                            break
                    except Exception:
                        pass
                    print("Invalid selection. Choose a number from the list or 0 to skip.")

                # Gender: only allow Male or Female (case-insensitive)
                gender = ''
                while True:
                    g = input("Gender (Male/Female) (or press Enter to skip): ").strip()
                    if g == '':
                        break
                    if g.lower() in ('male', 'female'):
                        gender = 'Male' if g.lower() == 'male' else 'Female'
                        break
                    print("Please enter only 'Male' or 'Female', or press Enter to skip.")

                # Region / City: free text (optional)
                region = input("Region / City (or press Enter to skip): ").strip()

                # Visitor type: only allow local/domestic/tourist
                vtype = ''
                while True:
                    vt = input("Visitor type (local/domestic/tourist) (or press Enter to skip): ").strip().lower()
                    if vt == '':
                        break
                    if vt in ('local', 'domestic', 'tourist'):
                        vtype = vt
                        break
                    print("Invalid input. Enter one of: local, domestic, tourist (or press Enter to skip).")

                # Explain marketing opt-in for first-time users
                print("\nMarketing opt-in allows us to email you promotional offers, park updates, and event notifications. You can change this later in My Account.")
                opt_bool = False
                while True:
                    opt = input("Marketing opt-in? (y/n): ").strip().lower()
                    if opt in ('y', 'n'):
                        opt_bool = True if opt == 'y' else False
                        break
                    print("Please enter 'y' or 'n'.")

                # Find the newly created user and persist demographics
                try:
                    u = Customer.load_by_email(email)
                    if u:
                        profile = {}
                        if age: profile['age_group'] = age
                        if gender: profile['gender'] = gender
                        if region: profile['region'] = region
                        if vtype: profile['visitor_type'] = vtype
                        profile['marketing_opt_in'] = opt_bool
                        u.update_profile(profile)
                        print("Demographics saved.")
                except Exception:
                    print("Failed to save demographics. You can update them later in My Account.")
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
    # MENUS
    def customer_menu(self, customer: Customer):
        # Delegate customer loop to the CustomerConsole facade
        self.customer_console.run(customer)

    def admin_menu(self, admin: Admin):
        # Delegate admin session loop to the AdminConsole facade
        self.admin_console.run(admin)
        
    # ==========================

# Helper to reconstruct a Merchandise-like object from a DB dict for display
def from_merch_dict(d):
    return AdminConsole_Merch_Helper(d['sku'], d['name'], d['price'], d['stock_quantity'])

from models import Merchandise as AdminConsole_Merch_Helper

if __name__ == "__main__":
    app = CLI()
    app.main_menu()