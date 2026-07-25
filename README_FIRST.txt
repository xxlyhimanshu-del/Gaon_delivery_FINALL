GAON GHAR DELIVERY — PERMANENT POSTGRESQL VERSION

WHAT IS PERMANENT NOW
- Products, orders, settings, logo, banner, QR and developer photo are stored in PostgreSQL.
- GitHub push, Render deploy, restart or sleep/wake will not reset this data.
- Uploaded images are stored inside the database as data URLs, so Render's temporary file storage is not used.

RENDER SETUP
1. Render Dashboard > New > PostgreSQL.
2. Create a database. Note: Render Free Postgres expires after 30 days; use a paid Postgres plan for truly long-term storage.
3. Open the database > Connect and copy its Internal Database URL.
4. Open your Web Service > Environment > Add Environment Variable.
5. Key: DATABASE_URL
6. Value: paste the Internal Database URL.
7. Also set SECRET_KEY and ADMIN_PASSWORD.
8. Save changes and redeploy.

IMPORTANT
- Do not set DATABASE_PATH on Render when DATABASE_URL is configured.
- The app automatically creates tables and starter products on a new empty PostgreSQL database.
- Admin backup now downloads a JSON backup instead of a SQLite .db file.
- Old deleted Render SQLite products cannot be recovered unless you have an earlier backup.
