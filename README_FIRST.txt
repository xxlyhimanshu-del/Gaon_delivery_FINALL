GAON GHAR DELIVERY — HIMANSHU EDITION

LOCAL RUN
1. Folder VS Code me open karo.
2. Terminal:
   pip install -r requirements.txt
   python app.py
3. Customer: http://127.0.0.1:5000
4. Admin: http://127.0.0.1:5000/admin/login
5. Local default admin password: 1234

PUBLIC DEPLOY SE PEHLE
- SECRET_KEY aur ADMIN_PASSWORD environment variables zaroor set karo.
- Public deployment par default password 1234 mat rakho.
- Uploaded photos aur SQLite database ko persistent storage chahiye; warna hosting restart/redeploy par data reset ho sakta hai.

FEATURES
- Premium mobile UI
- Splash: Developed by Himanshu
- Search + categories
- Quantity +/- cart
- 10% delivery charge
- Order tracking
- PWA install support
- Admin order dashboard + sales stats
- Product photo/category management
- Production Gunicorn config
- Health check: /health

DEVELOPER PHOTO
Apni photo ko static/developer.jpg naam se add karke footer/avatar template me image tag laga sakte ho. Abhi safe placeholder "H" diya hai.
