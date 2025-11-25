import sys
import unittest
import mongomock
from datetime import datetime, timedelta

# make sure parent folder is on sys.path
import os
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database import Database
from models import Schedule, Park, Merchandise, LineItem, Cart, Ticket
from services import AuthenticationManager, RefundRequest

# Configure Database to use mongomock for tests
def configure_in_memory_db():
    client = mongomock.MongoClient()
    Database.client = client
    Database.db = client['test_db']
    Database.users_col = Database.db['users']
    Database.parks_col = Database.db['parks']
    Database.merch_col = Database.db['merchandise']
    Database.orders_col = Database.db['orders']
    Database.carts_col = Database.db['carts']
    Database.tickets_col = Database.db['support_tickets']
    Database.reservations_col = Database.db['tickets']
    Database.audit_col = Database.db['audit_logs']

class BaseTest(unittest.TestCase):
    def setUp(self):
        configure_in_memory_db()

class TestScheduleModel(BaseTest):
    def test_schedule_availability_and_booking(self):
        s = Schedule('2030-01-01')
        self.assertTrue(s.is_available(1, 5))
        self.assertTrue(s.book_spots(3, 5))
        self.assertEqual(s.current_occupancy, 3)
        self.assertTrue(s.is_available(2, 5))
        self.assertTrue(s.book_spots(2, 5))
        self.assertEqual(s.current_occupancy, 5)
        self.assertFalse(s.book_spots(1, 5))

class TestDatabaseBooking(BaseTest):
    def test_atomic_book_spots_and_decrement(self):
        park_doc = {
            'park_id': 'PTEST',
            'name': 'Test Park',
            'location': 'Here',
            'description': 'desc',
            'max_capacity': 5,
            'schedules': [{'visit_date': '2030-01-01', 'current_occupancy': 0}]
        }
        Database.parks_col.insert_one(park_doc)

        ok = Database.atomic_book_spots('PTEST', '2030-01-01', 3)
        self.assertTrue(ok)
        doc = Database.parks_col.find_one({'park_id': 'PTEST'})
        sched = next(s for s in doc['schedules'] if s['visit_date'] == '2030-01-01')
        self.assertEqual(sched['current_occupancy'], 3)

        # overbook
        ok2 = Database.atomic_book_spots('PTEST', '2030-01-01', 3)
        self.assertFalse(ok2)

        # decrement
        dec = Database.decrement_schedule_occupancy('PTEST', '2030-01-01', 2)
        self.assertTrue(dec)
        doc2 = Database.parks_col.find_one({'park_id': 'PTEST'})
        sched2 = next(s for s in doc2['schedules'] if s['visit_date'] == '2030-01-01')
        self.assertEqual(sched2['current_occupancy'], 1)

class TestCartPersistence(BaseTest):
    def test_cart_save_and_load_via_customer(self):
        # create user in users_col for Customer constructor compatibility
        Database.users_col.insert_one({'user_id': 'cust01', 'name': 'A', 'email': 'a', 'password': 'p', 'role': 'Customer'})
        from models import Customer, Merchandise
        cust = Customer('cust01', 'A', 'a', 'p')

        merch = Merchandise('SKU1', 'M', 10.0, 5)
        li = LineItem('MERCH', merch, 2, merch.price, metadata={'sku': 'SKU1', 'stock_quantity': 5})
        cust.add_to_cart(li)

        # persisted
        saved = Database.get_cart('cust01')
        self.assertIsNotNone(saved)
        self.assertIn('items', saved)
        self.assertEqual(len(saved['items']), 1)

        # new customer instance should reconstruct cart
        cust2 = Customer('cust01', 'A', 'a', 'p')
        self.assertEqual(len(cust2.cart.items), 1)
        item = cust2.cart.items[0]
        self.assertEqual(item.item_type, 'MERCH')
        self.assertEqual(item.quantity, 2)

        # clear
        cust2.clear_cart()
        self.assertIsNone(Database.get_cart('cust01'))

class TestRefundAndTickets(BaseTest):
    def test_refund_allowed_and_denied(self):
        # prepare park and schedule
        park = {'park_id': 'PREF', 'name': 'P', 'location': 'L', 'description': 'D', 'max_capacity': 10, 'schedules': [{'visit_date': '2030-01-10', 'current_occupancy': 0}]}
        Database.parks_col.insert_one(park)
        # ticket dates: one in future >24h and one within next 12h
        future_date = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
        near_date = (datetime.now() + timedelta(hours=6)).strftime('%Y-%m-%d')

        # create tickets
        tid1, doc1 = Database.create_ticket('cust01', 'PREF', 'P', future_date, 50.0)
        tid2, doc2 = Database.create_ticket('cust01', 'PREF', 'P', near_date, 50.0)

        # prepare customer object
        Database.users_col.insert_one({'user_id': 'cust01', 'name': 'A', 'email': 'a', 'password': 'p', 'role': 'Customer'})
        from models import Customer
        cust = Customer('cust01', 'A', 'a', 'p')

        # create Ticket objects
        t1 = Ticket('cust01', 'P', future_date, 50.0, ticket_id=tid1, park_id='PREF')
        t2 = Ticket('cust01', 'P', near_date, 50.0, ticket_id=tid2, park_id='PREF')

        # refund for future -> True
        rr1 = RefundRequest(t1, cust)
        self.assertTrue(rr1.process_refund())
        # refund for near -> False (policy)
        rr2 = RefundRequest(t2, cust)
        self.assertFalse(rr2.process_refund())

class TestAuth(BaseTest):
    def test_register_and_login_logout(self):
        auth = AuthenticationManager()
        ok = auth.register_customer('Bob', 'bob@example', 'pw')
        self.assertTrue(ok)
        # duplicate email
        self.assertFalse(auth.register_customer('Bob', 'bob@example', 'pw'))
        user = auth.login('bob@example', 'pw')
        self.assertIsNotNone(user)
        self.assertEqual(auth.current_user.user_id, user.user_id)
        auth.logout()
        self.assertIsNone(auth.current_user)

if __name__ == '__main__':
    print('Running full test suite (unittest + mongomock)')
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        sys.exit(0)
    else:
        sys.exit(1)
