import aiosqlite
import os

DB_PATH = "data/bot.db"

async def init_db():
    os.makedirs("data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                price_per_sqm REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                service TEXT,
                width REAL,
                length REAL,
                area REAL,
                total_price REAL,
                delivery INTEGER DEFAULT 1,
                pickup_date TEXT,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                order_id INTEGER,
                rating INTEGER,
                text TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gallery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                before_file_id TEXT,
                after_file_id TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Default prices
        count = await db.execute("SELECT COUNT(*) FROM prices")
        row = await count.fetchone()
        if row[0] == 0:
            default_prices = [
                ("Стирка ковра", 250),
                ("Химчистка", 350),
                ("Удаление пятен", 400),
                ("Чистка паласа", 200),
                ("Стирка дорожки", 180),
            ]
            await db.executemany("INSERT INTO prices (service, price_per_sqm) VALUES (?, ?)", default_prices)

        await db.commit()

async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def get_prices():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, service, price_per_sqm FROM prices")
        return await cur.fetchall()

async def update_price(price_id: int, new_price: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE prices SET price_per_sqm=? WHERE id=?", (new_price, price_id))
        await db.commit()

async def create_order(user_id, username, full_name, phone, service, width, length, area, total_price, delivery, pickup_date):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO orders (user_id, username, full_name, phone, service, width, length, area, total_price, delivery, pickup_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, full_name, phone, service, width, length, area, total_price, delivery, pickup_date))
        await db.commit()
        return cur.lastrowid

async def get_orders(status=None, limit=20):
    async with aiosqlite.connect(DB_PATH) as db:
        if status:
            cur = await db.execute("SELECT * FROM orders WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
        else:
            cur = await db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
        return await cur.fetchall()

async def get_order(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        return await cur.fetchone()

async def update_order_status(order_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        await db.commit()

async def get_user_orders(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (user_id,))
        return await cur.fetchall()

async def create_review(user_id, username, full_name, order_id, rating, text):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO reviews (user_id, username, full_name, order_id, rating, text)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, username, full_name, order_id, rating, text))
        await db.commit()
        return cur.lastrowid

async def get_reviews(status=None):
    async with aiosqlite.connect(DB_PATH) as db:
        if status:
            cur = await db.execute("SELECT * FROM reviews WHERE status=? ORDER BY created_at DESC", (status,))
        else:
            cur = await db.execute("SELECT * FROM reviews ORDER BY created_at DESC")
        return await cur.fetchall()

async def get_review(review_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM reviews WHERE id=?", (review_id,))
        return await cur.fetchone()

async def update_review_status(review_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reviews SET status=? WHERE id=?", (status, review_id))
        await db.commit()

async def get_gallery():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM gallery ORDER BY created_at DESC LIMIT 10")
        return await cur.fetchall()

async def add_gallery_item(before_file_id, after_file_id, description):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO gallery (before_file_id, after_file_id, description) VALUES (?, ?, ?)",
                         (before_file_id, after_file_id, description))
        await db.commit()

async def get_promotions():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM promotions WHERE active=1 ORDER BY created_at DESC")
        return await cur.fetchall()

async def add_promotion(title, description):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO promotions (title, description) VALUES (?, ?)", (title, description))
        await db.commit()

async def toggle_promotion(promo_id: int, active: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE promotions SET active=? WHERE id=?", (active, promo_id))
        await db.commit()
