# National Park Manager Application

This repository contains a small command-line application for managing state parks, selling tickets and merchandise, and customer/admin flows.

## Requirements
- Python 3.8+ (3.10+ recommended)
- A running MongoDB instance (default URI configured in `database.py`)

Optional (for in-terminal QR rendering):
- `segno` — to render QR codes in the terminal

> Note: Tests live in `tests/` and use an in-memory MongoDB mock (mongomock). Testing dependencies are optional and not included by default.

## Quick start
1. Open a terminal and change to the project directory. 

2. (Recommended) Create and activate a virtual environment:

```cmd
python -m venv .venv
.venv\Scripts\activate
```

3. Install runtime dependencies:

```cmd
pip install -r requirements.txt
```

4. Make sure MongoDB is running locally. By default the app expects `mongodb://localhost:27017/` and uses database `park_system_db`.

- On Windows if you installed MongoDB as a service you can start it from Services or run:

```cmd
net start MongoDB
```

- If you use a remote/Atlas MongoDB, update `MONGO_URI` in `database.py` accordingly.

5. Run the CLI application:

```cmd
python main.py
```

On first run the app will seed initial data (users, parks, merchandise) if the database is empty.

## Using the CLI
- Login with seeded users or register a new account.
  - Seeded Admin: `email: admin@example.com`, `password: admin123`.
  - Seeded Customers: `email: john.doe@example.com` / `password: 123`, `email: jane.smith@example.com` / `password: 123`.
- Customers can browse parks, add tickets/merch to cart, checkout, view tickets, and contact support.
- Admin can manage parks, merchandise, view reports and resolve support tickets.

## QR Codes
- The `View Tickets` flow attempts to render a QR code in the terminal using `segno`. If `segno` is not installed, the app will print the ticket id and an instruction to install `segno`.

To enable in-terminal QR rendering and display Unicode properly on Windows:

```cmd
pip install segno
chcp 65001
```

If you prefer graphical QR images, the app can be extended to save QR PNGs and open them with the OS image viewer.

## Tests (optional)
- Tests are in `tests/run_tests.py` and use `mongomock` to emulate MongoDB in-memory. To run the tests you will need to install testing dependencies (not included in `requirements.txt` by default).

Example (optional):

```cmd
pip install mongomock
python -m tests.run_tests
```

## Configuration
- DB connection: `database.py` (variable `MONGO_URI`).
- Change defaults there if you need to point to a different MongoDB server or add authentication.

## Troubleshooting
- "Cannot connect to MongoDB": make sure the MongoDB service is running and `MONGO_URI` is correct.
- QR not rendering: ensure `segno` is installed and use a UTF-8 capable terminal (on Windows run `chcp 65001`).




