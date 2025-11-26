"""
Domain model definitions for the State Park System.

This module contains the core business objects used by the
application: parks, schedules, tickets, merchandise, carts and
orders. Models expose simple serialization helpers and light
business logic (availability checks, stock updates) while heavy
persistence logic is delegated to `database.py`.
"""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from database import Database

# ==========================
# DOMAIN CLASSES
# ==========================

class Schedule:
    """Represents a single visit date / schedule for a Park.

    Holds the `visit_date` string and a `current_occupancy` counter.
    Note: park-level capacity is enforced by Park/Database logic; the
    Schedule object focuses on occupancy tracking and convenience
    helpers such as `is_available`/`book_spots`.
    """

    def __init__(self, visit_date, current_occupancy=0, max_capacity=None, **kwargs):
        # Accept legacy `max_capacity` if present in DB documents and ignore it.
        self.visit_date = visit_date
        self.current_occupancy = current_occupancy

    def is_available(self, quantity, park_max_capacity):
        return (self.current_occupancy + quantity) <= park_max_capacity

    def book_spots(self, quantity, park_max_capacity):
        if self.is_available(quantity, park_max_capacity):
            self.current_occupancy += quantity
            return True
        return False
    
    def to_dict(self):
        return {
            "visit_date": self.visit_date,
            "current_occupancy": self.current_occupancy
        }

    def __str__(self):
        return f"{self.visit_date} | Occupancy: {self.current_occupancy}"

class Park:
    """Domain object representing a Park.

    A Park contains metadata (name, location, description), a
    park-level `max_capacity` and a list of `Schedule` objects.
    Persistence and ID generation are handled by `Database` and the
    `add_park` factory method.
    """

    def __init__(self, park_id, name, location, description, schedules=None, max_capacity=0, ticket_price=None, _id=None):
        # Accept optional MongoDB `_id` when reconstructing from DB dicts
        self._id = _id
        self.park_id = park_id
        self.name = name
        self.location = location
        self.description = description
        # park-level maximum capacity (applies to all schedules unless otherwise handled)
        self.max_capacity = max_capacity or 0
        # per-park ticket price (set by DB or admin). Keep None if not provided.
        self.ticket_price = ticket_price
        # schedules is a list of Schedule Objects
        self.schedules = [Schedule(**s) if isinstance(s, dict) else s for s in (schedules or [])]

    def add_schedule(self, schedule):
        # Avoid duplicate schedules for the same date
        if any(s.visit_date == schedule.visit_date for s in self.schedules):
            raise ValueError(f"Schedule already exists for date {schedule.visit_date}")
        self.schedules.append(schedule)
    
    def save_schedules(self):
        """Persist schedule changes to DB"""
        sched_list = [s.to_dict() for s in self.schedules]
        Database.update_park_schedule(self.park_id, sched_list)

    def to_dict(self):
        return {
            "park_id": self.park_id, "name": self.name, "location": self.location,
            "description": self.description, "max_capacity": self.max_capacity,
            "ticket_price": self.ticket_price,
            "schedules": [s.to_dict() for s in self.schedules]
        }

    def find_schedule(self, visit_date):
        for s in self.schedules:
            if s.visit_date == visit_date:
                return s
        return None

    def remove_schedule(self, visit_date):
        s = self.find_schedule(visit_date)
        if not s:
            raise ValueError("Schedule not found")
        self.schedules.remove(s)

    def update_max_capacity(self, new_capacity):
        if new_capacity < 0:
            raise ValueError("Capacity must be a non-negative integer")
        # ensure no schedule's current occupancy exceeds new capacity
        for s in self.schedules:
            if s.current_occupancy > new_capacity:
                raise ValueError("New capacity cannot be less than existing schedule occupancy")
        self.max_capacity = new_capacity
        # persist change
        self.save()

    def update_name(self, new_name):
        if not new_name:
            raise ValueError("Name cannot be empty")
        self.name = new_name

    def update_location(self, new_location):
        if not new_location:
            raise ValueError("Location cannot be empty")
        self.location = new_location

    def update_description(self, new_description):
        # description may be empty; accept but keep as-is if None
        self.description = new_description

    def save(self):
        """Persist the park document (name, location, description, schedules)."""
        try:
            Database.parks_col.update_one({'park_id': self.park_id}, {'$set': self.to_dict()}, upsert=True)
        except Exception:
            # As a fallback, try replace_one
            try:
                Database.parks_col.replace_one({'park_id': self.park_id}, self.to_dict(), upsert=True)
            except Exception:
                raise

    def delete(self):
        try:
            Database.parks_col.delete_one({'park_id': self.park_id})
        except Exception:
            raise

    @classmethod
    def add_park(cls, name, location, description, schedules=None, max_capacity=0, ticket_price=None):
        """Create a new Park with generated park_id, attach schedules and persist.

        `schedules` may be a list of Schedule objects or list of dicts with keys visit_date/max_capacity.
        Returns the created Park instance.
        """
        try:
            existing = Database.parks_col.count_documents({})
        except Exception:
            existing = 0
        park_num = existing + 1
        park_id = f"P0{park_num}"

        # Normalize schedules to Schedule objects
        sched_objs = []
        for s in (schedules or []):
            if isinstance(s, Schedule):
                sched_objs.append(s)
            elif isinstance(s, dict):
                sched_objs.append(Schedule(s.get('visit_date'), s.get('current_occupancy', 0)))
            else:
                # assume tuple/list
                try:
                    visit_date = s[0]
                    occ = int(s[1]) if len(s) > 1 else 0
                    sched_objs.append(Schedule(visit_date, occ))
                except Exception:
                    raise ValueError("Invalid schedule format")

        p = cls(park_id, name, location, description, schedules=sched_objs, max_capacity=max_capacity, ticket_price=ticket_price)
        p.save()
        return p

    @classmethod
    def load_by_park_id(cls, park_id):
        """Load a Park instance by its `park_id` or return None."""
        doc = Database.parks_col.find_one({'park_id': park_id})
        if not doc:
            return None
        return cls(**doc)

    @classmethod
    def get_all(cls):
        """Return all parks as Park instances."""
        docs = Database.get_all_parks()
        return [cls(**d) for d in docs]

    @classmethod
    def try_book(cls, park_id, visit_date, qty):
        """Attempt to book `qty` spots for a park schedule.

        Returns the same values as `Database.atomic_book_spots`:
          True  -> success
          False -> insufficient capacity
          None  -> park/schedule not found
        """
        return Database.atomic_book_spots(park_id, visit_date, qty)

    @classmethod
    def decrement_occupancy(cls, park_id, visit_date, qty):
        """Decrement occupancy for a park schedule via DB helper."""
        return Database.decrement_schedule_occupancy(park_id, visit_date, qty)

class Merchandise:
    """Simple merchandise item with stock management helpers.

    Keeps `sku`, `name`, `price` and `stock_quantity`. Methods update
    the DB via `Database.update_merch_stock` when stock changes.
    """

    def __init__(self, sku, name, price, stock_quantity, _id=None):
        self.sku = sku
        self.name = name
        self.price = price
        self.stock_quantity = stock_quantity
        self._id = _id

    def decrease_stock(self, qty):
        if self.stock_quantity >= qty:
            self.stock_quantity -= qty
            Database.update_merch_stock(self.sku, self.stock_quantity)
            return True
        return False

    def increase_stock(self, qty):
        self.stock_quantity += qty
        Database.update_merch_stock(self.sku, self.stock_quantity)

    def to_dict(self):
        return {
            "sku": self.sku,
            "name": self.name,
            "price": self.price,
            "stock_quantity": self.stock_quantity
        }

    def save(self):
        """Persist (insert or update) this merchandise item."""
        try:
            Database.merch_col.update_one({'sku': self.sku}, {'$set': self.to_dict()}, upsert=True)
        except Exception:
            try:
                Database.merch_col.replace_one({'sku': self.sku}, self.to_dict(), upsert=True)
            except Exception:
                raise

    def delete(self):
        try:
            Database.merch_col.delete_one({'sku': self.sku})
        except Exception:
            raise

    def __str__(self):
        return f"{self.name} (${self.price}) - Stock: {self.stock_quantity}"

    @classmethod
    def load_by_sku(cls, sku):
        doc = Database.merch_col.find_one({'sku': sku})
        if not doc:
            return None
        return cls(doc.get('sku'), doc.get('name'), doc.get('price'), doc.get('stock_quantity'), _id=doc.get('_id'))

    @classmethod
    def get_all(cls):
        """Return all merchandise items as Merchandise instances."""
        docs = Database.get_all_merchandise()
        return [cls(d.get('sku'), d.get('name'), d.get('price'), d.get('stock_quantity'), _id=d.get('_id')) for d in docs]

class Ticket:
    """Lightweight in-memory representation of a purchased ticket.

    Persistent tickets are stored in the `tickets` collection via
    `Database.create_ticket`. This class is useful for session-level
    bookkeeping and for displaying ticket summaries to the user.
    """

    def __init__(self, owner_id, park_name, visit_date, price, ticket_id=None, status="CONFIRMED", park_id=None):
        self.ticket_id = ticket_id if ticket_id else str(uuid.uuid4())[:8]
        self.owner_id = owner_id
        self.park_id = park_id
        self.park_name = park_name
        self.visit_date = visit_date
        self.status = status
        self.qr_code = f"QR-{self.ticket_id}"
        self.price = price

    def cancel(self):
        self.status = "CANCELLED"
        # Note: In a full app, we would update the Ticket collection status here.

    def __str__(self):
        return f"[ID: {self.ticket_id}] {self.park_name} on {self.visit_date} ({self.status})"

    @classmethod
    def load_by_id(cls, ticket_id):
        doc = Database.reservations_col.find_one({'ticket_id': ticket_id})
        if not doc:
            return None
        return cls(doc.get('owner_id'), doc.get('park_name'), doc.get('visit_date'), doc.get('price'), ticket_id=doc.get('ticket_id'), status=doc.get('status'), park_id=doc.get('park_id'))

    @classmethod
    def create(cls, owner_id, park_id, park_name, visit_date, price):
        """Create persistent ticket(s) and return ticket id and Ticket instance/doc."""
        tid, doc = Database.create_ticket(owner_id, park_id, park_name, visit_date, price)
        return tid, cls(doc.get('owner_id'), doc.get('park_name'), doc.get('visit_date'), doc.get('price'), ticket_id=doc.get('ticket_id'), status=doc.get('status'), park_id=doc.get('park_id'))

    @classmethod
    def find_by_owner(cls, owner_id, status=None):
        """Return list of ticket documents for owner (optionally filtered by status)."""
        query = {'owner_id': owner_id}
        if status:
            query['status'] = status
        try:
            docs = list(Database.reservations_col.find(query))
        except Exception:
            return []
        return docs

    @classmethod
    def update_visit_date(cls, ticket_id, new_date):
        """Update the visit_date field for a persistent ticket."""
        try:
            Database.reservations_col.update_one({'ticket_id': ticket_id}, {'$set': {'visit_date': new_date}})
            return True
        except Exception:
            return False

    @classmethod
    def set_status(cls, ticket_id, status):
        """Set the persistent ticket status via Database helper.

        Returns True on success, False on error.
        """
        try:
            Database.update_ticket_status(ticket_id, status)
            return True
        except Exception:
            return False

class LineItem:
    """Represents an item in a Cart or Order.

    `item_obj` may be a `Merchandise` object or a `Park` for tickets;
    `metadata` stores serializable fields used for persistence.
    """

    def __init__(self, item_type, item_obj, quantity, unit_price, metadata=None):
        self.item_type = item_type
        self.item_obj = item_obj # This is an object (Merch or Park)
        self.quantity = quantity
        self.unit_price = unit_price
        self.metadata = metadata

    @property
    def total_price(self):
        return self.unit_price * self.quantity

    def to_dict(self):
        """Serialize for Order storage"""
        item_name = self.item_obj.name
        return {
            "item_type": self.item_type,
            "item_name": item_name,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "metadata": self.metadata
        }

class Cart:
    """In-memory shopping cart for a Customer session.

    Items are `LineItem` objects. The `Customer` class persists a
    serialized version of the cart so that it can be restored across
    runs.
    """

    def __init__(self):
        self.items = []

    def add_item(self, line_item):
        self.items.append(line_item)

    def clear(self):
        self.items = []

    def get_total(self):
        return sum(item.total_price for item in self.items)

class Order:
    """Represents a completed purchase order.

    `line_items` should be a list of serialized dictionaries suitable
    for storage in the `orders` collection.
    """

    def __init__(self, user_id, line_items, total_cost):
        self.order_id = str(uuid.uuid4())[:8]
        self.user_id = user_id
        self.line_items = line_items # list of dicts (serialized LineItems)
        self.total_cost = total_cost
        self.date = datetime.now()
        self.payment_status = "PAID"

    def to_dict(self):
        return {
            "order_id": self.order_id,
            "user_id": self.user_id,
            "line_items": self.line_items,
            "total_cost": self.total_cost,
            "date": self.date,
            "payment_status": self.payment_status
        }

    def save(self):
        """Persist this order to the orders collection."""
        Database.add_order(self.to_dict())

    @classmethod
    def load_by_id(cls, order_id):
        doc = Database.orders_col.find_one({'order_id': order_id})
        if not doc:
            return None
        o = cls(doc.get('user_id'), doc.get('line_items'), doc.get('total_cost'))
        o.order_id = doc.get('order_id')
        o.date = doc.get('date')
        o.payment_status = doc.get('payment_status', 'PAID')
        return o

    @classmethod
    def get_all(cls):
        """Return raw order documents for reporting and analysis."""
        try:
            return Database.get_all_orders()
        except Exception:
            return []

class SupportTicket:
    """Support ticket created by a user to report issues.

    Tickets are simple records stored in the `support_tickets` collection
    and include a free-text description and an optional resolution.
    """

    def __init__(self, user_id, description, status="OPEN", resolution="", id=None):
        self.id = id if id else str(uuid.uuid4())[:6]
        self.user_id = user_id
        self.description = description
        self.status = status
        self.resolution = resolution

    def resolve(self, notes):
        self.status = "RESOLVED"
        self.resolution = notes
        Database.update_support_ticket(self.id, notes)

    def save(self):
        """Persist this support ticket to the support_tickets collection."""
        try:
            Database.add_support_ticket(self.to_dict())
        except Exception:
            raise

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id, "description": self.description,
            "status": self.status, "resolution": self.resolution
        }

    @classmethod
    def get_open(cls):
        try:
            return list(Database.tickets_col.find({'status': 'OPEN'}))
        except Exception:
            return []

    @classmethod
    def load_by_id(cls, ticket_id):
        doc = Database.tickets_col.find_one({'id': ticket_id})
        if not doc:
            return None
        return cls(doc.get('user_id'), doc.get('description'), status=doc.get('status'), resolution=doc.get('resolution'), id=doc.get('id'))

# ==========================
# USER HIERARCHY
# ==========================
class User(ABC):
    """Base abstract class for application users.

    Subclasses must implement `get_role()` to indicate their role in
    the system (e.g., "Customer" or "Admin").
    """

    def __init__(self, user_id, name, email, password):
        self.user_id = user_id
        self.name = name
        self.email = email
        self.password = password

    @abstractmethod
    def get_role(self):
        pass

    def to_dict(self):
        return {
            "user_id": self.user_id, "name": self.name, 
            "email": self.email, "password": self.password, 
            "role": self.get_role()
        }

class Customer(User):
    """Customer user with cart persistence and session tickets.

    On construction the Customer attempts to load a persisted cart
    from the `carts` collection and reconstructs `LineItem` objects
    where possible. The `add_to_cart` and `clear_cart` helpers also
    synchronize the persisted cart state.
    """

    def __init__(self, user_id, name, email, password, age_group=None, gender=None, region=None, visitor_type=None, marketing_opt_in=False):
        super().__init__(user_id, name, email, password)
        # Demographic/profile fields
        self.age_group = age_group
        self.gender = gender
        self.region = region
        self.visitor_type = visitor_type
        self.marketing_opt_in = bool(marketing_opt_in)

        self.cart = Cart()
        self.tickets = [] # In-memory list of current session tickets
        # Load persisted cart (if any)
        try:
            saved = Database.get_cart(self.user_id)
            if saved and saved.get('items'):
                reconstructed = []
                for it in saved.get('items', []):
                    it_type = it.get('item_type')
                    qty = it.get('quantity', 1)
                    unit = it.get('unit_price', 0.0)
                    meta = it.get('metadata') or {}
                    if it_type == 'MERCH':
                        # Reconstruct a Merchandise object from stored metadata if available
                        sku = meta.get('sku') or it.get('metadata', {}).get('sku') or None
                        name = it.get('item_name')
                        price = it.get('unit_price', 0.0)
                        stock = meta.get('stock_quantity') or 0
                        merch_obj = None
                        try:
                            from models import Merchandise as _M
                            merch_obj = _M(sku, name, price, stock)
                        except Exception:
                            merch_obj = None
                        li = LineItem('MERCH', merch_obj, qty, unit, meta)
                        reconstructed.append(li)
                    elif it_type == 'TICKET':
                        # Reconstruct park object from park_id in metadata
                        park_obj = None
                        park_id = meta.get('park_id') or meta.get('park')
                        if park_id:
                            park_doc = Database.parks_col.find_one({'park_id': park_id})
                            if park_doc:
                                try:
                                    park_obj = Park(**park_doc)
                                except Exception:
                                    park_obj = None
                        # fallback to item_name
                        li = LineItem('TICKET', park_obj, qty, unit, meta)
                        reconstructed.append(li)
                    else:
                        # Generic fallback
                        li = LineItem(it.get('item_type'), None, qty, unit, meta)
                        reconstructed.append(li)
                self.cart.items = reconstructed
        except Exception:
            pass

    def get_role(self):
        return "Customer"

    def add_to_cart(self, line_item):
        """Add a LineItem to the in-memory cart and persist the cart to DB."""
        self.cart.add_item(line_item)
        try:
            Database.save_cart(self.user_id, self._serialize_cart())
        except Exception:
            pass

    def clear_cart(self):
        """Clear in-memory cart and remove persisted cart."""
        self.cart.clear()
        try:
            Database.delete_cart(self.user_id)
        except Exception:
            pass

    def _serialize_cart(self):
        """Return a serializable list of cart line-item dicts suitable for DB storage."""
        out = []
        for it in self.cart.items:
            d = {
                'item_type': it.item_type,
                'item_name': getattr(it.item_obj, 'name', None) if it.item_obj is not None else None,
                'quantity': it.quantity,
                'unit_price': it.unit_price,
                'metadata': None
            }
            # Normalize metadata for persistence
            meta = it.metadata or {}
            if it.item_type == 'TICKET':
                # store only serializable fields
                meta_serial = {
                    'date': meta.get('date'),
                    'park_id': (getattr(it.item_obj, 'park_id', None) if it.item_obj else meta.get('park_id')),
                    'park_name': (getattr(it.item_obj, 'name', None) if it.item_obj else meta.get('park_name'))
                }
                d['metadata'] = meta_serial
            elif it.item_type == 'MERCH':
                # store sku/name/price/stock if available
                merch = it.item_obj
                meta_serial = {
                    'sku': getattr(merch, 'sku', None),
                    'stock_quantity': getattr(merch, 'stock_quantity', None)
                }
                d['metadata'] = meta_serial
            else:
                d['metadata'] = meta
            out.append(d)
        return out

    def to_dict(self):
        base = super().to_dict()
        base.update({
            'age_group': self.age_group,
            'gender': self.gender,
            'region': self.region,
            'visitor_type': self.visitor_type,
            'marketing_opt_in': bool(self.marketing_opt_in)
        })
        return base

    @classmethod
    def load_by_id(cls, user_id):
        doc = Database.get_user_by_id(user_id)
        if not doc:
            return None
        return cls(
            doc.get('user_id'),
            doc.get('name'),
            doc.get('email'),
            doc.get('password'),
            age_group=doc.get('age_group'),
            gender=doc.get('gender'),
            region=doc.get('region'),
            visitor_type=doc.get('visitor_type'),
            marketing_opt_in=doc.get('marketing_opt_in', False)
        )

    @classmethod
    def load_by_email(cls, email):
        doc = Database.get_user(email)
        if not doc:
            return None
        return cls.load_by_id(doc.get('user_id'))

    @classmethod
    def count_customers(cls):
        try:
            return Database.users_col.count_documents({"role": "Customer"})
        except Exception:
            return 0

    def save(self):
        """Persist this customer to the users collection."""
        Database.add_user(self)

    def update_profile(self, profile_fields: dict):
        Database.update_user_profile(self.user_id, profile_fields)
        for k, v in profile_fields.items():
            setattr(self, k, v)

class Admin(User):
    def get_role(self):
        return "Admin"

    @classmethod
    def load_by_email(cls, email):
        doc = Database.get_user(email)
        if not doc or doc.get('role') != 'Admin':
            return None
        return cls(doc.get('user_id'), doc.get('name'), doc.get('email'), doc.get('password'))


class Audit:
    """Small helper to centralise audit log persistence behind models.

    Services and other higher-level components should call
    `Audit.log(entry)` and `Audit.get_all()` rather than touching
    `Database` directly.
    """

    @staticmethod
    def log(entry):
        Database.log_audit(entry)

    @staticmethod
    def get_all():
        return Database.get_audit_logs()