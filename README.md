# Mkulima Direct — Setup & Configuration Guide

Mkulima Direct is a farm-to-buyer marketplace built with Flask (Python) and PostgreSQL.
This guide walks you through getting the full system running locally, including M-Pesa Daraja STK Push payments.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Project Structure](#2-project-structure)
3. [Database Setup](#3-database-setup)
4. [Environment Variables](#4-environment-variables)
5. [Install Python Dependencies](#5-install-python-dependencies)
6. [M-Pesa Daraja Setup](#6-m-pesa-daraja-setup)
7. [Exposing the Callback URL (ngrok)](#7-exposing-the-callback-url-ngrok)
8. [Running the Backend](#8-running-the-backend)
9. [Running the Frontend](#9-running-the-frontend)
10. [Testing M-Pesa Payments](#10-testing-m-pesa-payments)
11. [Going Live (Production)](#11-going-live-production)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

Make sure the following are installed on your machine before starting:

| Tool | Version | Download |
|------|---------|----------|
| Python | 3.10+ | https://python.org |
| PostgreSQL | 14+ | https://postgresql.org |
| Apache (XAMPP or standalone) | Any | https://httpd.apache.org |
| ngrok (for M-Pesa callback) | Any | https://ngrok.com |
| Git | Any | https://git-scm.com |

---

## 2. Project Structure

```
MkulimaDirect/
├── backend/
│   ├── app.py              # Flask API — all routes
│   ├── db.py               # PostgreSQL connection helper
│   ├── mpesa.py            # M-Pesa Daraja STK Push helper
│   └── requirements.txt    # Python dependencies
├── buyer/
│   ├── dashboard.html      # Marketplace
│   ├── cart.html           # Cart + M-Pesa payment modal
│   └── order-history.html
├── farmer/
│   ├── dashboard.html
│   ├── add-produce.html
│   ├── my-produce.html
│   └── orders.html
├── admin/
│   └── dashboard.html
├── auth/
│   ├── login.html
│   └── register.html
├── tables.sql              # Database schema
├── .env                    # Credentials (never commit this)
├── .gitignore
└── README.md
```

---

## 3. Database Setup

### Step 1 — Start PostgreSQL

Make sure your PostgreSQL server is running. On Windows you can start it from the Services panel or pgAdmin.

### Step 2 — Create the database and tables

Open **pgAdmin** or **psql** and run:

```sql
CREATE DATABASE mkulima;
```

Then connect to the new database and run the full schema:

```bash
psql -U postgres -d mkulima -f tables.sql
```

Or paste the contents of `tables.sql` directly into the pgAdmin query tool.

### Step 3 — Verify

```sql
\c mkulima
\dt
```

You should see tables: `farmer`, `buyer`, `admin`, `produce`, `cart`, `orders`, `order_details`, `payments`, `farmer_ratings`, `complaints`, `audit_logs`.

---

## 4. Environment Variables

All credentials are stored in a single `.env` file at the project root. **Never commit this file to Git** — it is already listed in `.gitignore`.

Open `.env` and fill in your values:

```env
# ── M-Pesa Daraja Credentials ──────────────────────────────────────
MPESA_CONSUMER_KEY=YOUR_CONSUMER_KEY
MPESA_CONSUMER_SECRET=YOUR_CONSUMER_SECRET
MPESA_SHORTCODE=174379
MPESA_PASSKEY=bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919
MPESA_CALLBACK_URL=https://xxxx.ngrok.io/api/mpesa/callback
MPESA_BASE_URL=https://sandbox.safaricom.co.ke

# ── Database ────────────────────────────────────────────────────────
DB_HOST=localhost
DB_NAME=mkulima
DB_USER=postgres
DB_PASSWORD=1234
DB_PORT=5432

# ── Gmail / Email ───────────────────────────────────────────────────
GMAIL_ADDRESS=your_gmail@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

> The `MPESA_SHORTCODE` and `MPESA_PASSKEY` above are Safaricom's **sandbox** defaults and work out of the box for testing. Only replace them when going live.

---

## 5. Install Python Dependencies

Navigate to the `backend` folder and install all required packages:

```bash
cd backend
pip install -r requirements.txt
```

This installs:

- `flask` — web framework
- `flask-cors` — allows the HTML frontend to call the API
- `psycopg2-binary` — PostgreSQL driver
- `requests` — used to call the Safaricom Daraja API
- `python-dotenv` — loads the `.env` file automatically
- `werkzeug` — password hashing

---

## 6. M-Pesa Daraja Setup

### Step 1 — Create a Daraja account

Go to [https://developer.safaricom.co.ke](https://developer.safaricom.co.ke) and sign up for a free account.

### Step 2 — Create an app

1. Click **My Apps** → **Add a new app**
2. Give it a name (e.g. "Mkulima Direct")
3. Select the **Lipa Na M-Pesa Sandbox** product
4. Click **Create App**

### Step 3 — Copy your credentials

On the app detail page you will see:

- **Consumer Key** — copy this into `.env` as `MPESA_CONSUMER_KEY`
- **Consumer Secret** — copy this into `.env` as `MPESA_CONSUMER_SECRET`

> The sandbox shortcode (`174379`) and passkey are already pre-filled in `.env` and work without any changes.

### Step 4 — Set the callback URL

The callback URL is where Safaricom sends the payment result after the buyer enters their PIN.
It **must be HTTPS and publicly reachable**. See Section 7 below for how to set this up locally with ngrok.

Once you have your ngrok URL, update `.env`:

```env
MPESA_CALLBACK_URL=https://xxxx.ngrok.io/api/mpesa/callback
```

---

## 7. Exposing the Callback URL (ngrok)

Safaricom cannot reach `localhost` directly, so you need to tunnel your local Flask server to a public URL.

### Step 1 — Install ngrok

Download from [https://ngrok.com/download](https://ngrok.com/download) and unzip it.

On Windows, you can also install it with:

```bash
choco install ngrok
```

Or simply download the `.exe` and place it anywhere on your PATH.

### Step 2 — Sign up and add your auth token

Create a free account at [https://ngrok.com](https://ngrok.com), then run:

```bash
ngrok config add-authtoken YOUR_NGROK_TOKEN
```

### Step 3 — Start the tunnel

With your Flask server running on port 5000, open a **second terminal** and run:

```bash
ngrok http 5000
```

You will see output like:

```
Forwarding   https://a1b2c3d4.ngrok.io -> http://localhost:5000
```

### Step 4 — Update your .env

Copy the `https://` URL and update `.env`:

```env
MPESA_CALLBACK_URL=https://a1b2c3d4.ngrok.io/api/mpesa/callback
```

Then **restart Flask** so it picks up the new value.

> ngrok URLs change every time you restart it on the free plan. Remember to update `.env` and restart Flask each time.

---

## 8. Running the Backend

```bash
cd backend
python app.py
```

You should see:

```
Mkulima Direct API is running on http://localhost:5000
```

Leave this terminal open while using the app.

---

## 9. Running the Frontend

The HTML files are served by Apache. Make sure Apache is running, then open your browser and go to:

```
http://localhost/MkulimaDirect/index.html
```

If you placed the project in a different folder inside `htdocs`, adjust the path accordingly.

---

## 10. Testing M-Pesa Payments

Use Safaricom's sandbox test credentials — no real money is involved.

### Sandbox test phone number

```
0708374149
```

This is Safaricom's official sandbox number. Use it when prompted for the M-Pesa phone number in the payment modal.

### Full payment flow

1. Log in as a buyer
2. Browse the marketplace and add items to your cart
3. Go to **My Cart** and click **Pay with M-Pesa**
4. Enter `0708374149` as the phone number and a delivery address
5. Click **Send STK Push**
6. The modal switches to "Waiting for M-Pesa PIN..." and polls every 3 seconds
7. Safaricom's sandbox automatically confirms the payment after a few seconds
8. The modal shows **Payment Confirmed!**

### Checking the result in the database

```sql
SELECT * FROM payments ORDER BY payment_date DESC LIMIT 5;
SELECT * FROM orders ORDER BY order_date DESC LIMIT 5;
```

A successful payment will show `payment_status = 'Completed'` and `order_status = 'Confirmed'`.

---

## 11. Going Live (Production)

When you are ready to accept real payments, make the following changes in `.env`:

```env
# Switch to production API
MPESA_BASE_URL=https://api.safaricom.co.ke

# Replace with your real paybill or till credentials
MPESA_SHORTCODE=YOUR_PRODUCTION_SHORTCODE
MPESA_PASSKEY=YOUR_PRODUCTION_PASSKEY
MPESA_CONSUMER_KEY=YOUR_PRODUCTION_CONSUMER_KEY
MPESA_CONSUMER_SECRET=YOUR_PRODUCTION_CONSUMER_SECRET

# Must be a real domain with a valid SSL certificate
MPESA_CALLBACK_URL=https://yourdomain.com/api/mpesa/callback
```

Also make sure to:

- Deploy Flask behind a production WSGI server (e.g. **Gunicorn** + **Nginx**)
- Use a real SSL certificate (e.g. Let's Encrypt / Certbot)
- Set `debug=False` in `app.py`

---

## 12. Troubleshooting

### Flask won't start — ModuleNotFoundError

```bash
pip install -r backend/requirements.txt
```

Make sure you are inside the `backend` folder when running this.

---

### M-Pesa returns "Invalid Access Token"

- Double-check `MPESA_CONSUMER_KEY` and `MPESA_CONSUMER_SECRET` in `.env`
- Make sure you are using sandbox credentials against `https://sandbox.safaricom.co.ke`
- Tokens expire after 1 hour — the app fetches a fresh one on every request, so simply retry

---

### Callback is never received (payment stays "Pending")

- Confirm ngrok is running: `ngrok http 5000`
- Confirm `MPESA_CALLBACK_URL` in `.env` matches the current ngrok HTTPS URL
- Restart Flask after updating `.env`
- Test the callback URL manually:

```bash
curl -X POST https://your-ngrok-url.ngrok.io/api/mpesa/callback \
  -H "Content-Type: application/json" \
  -d "{\"Body\":{\"stkCallback\":{\"ResultCode\":0,\"MerchantRequestID\":\"test\",\"CallbackMetadata\":{\"Item\":[{\"Name\":\"MpesaReceiptNumber\",\"Value\":\"TEST123\"},{\"Name\":\"PhoneNumber\",\"Value\":\"254708374149\"},{\"Name\":\"Amount\",\"Value\":1}]}}}}"
```

---

### Database connection failed

- Make sure PostgreSQL is running
- Verify the credentials in `.env` match your local PostgreSQL setup
- Default PostgreSQL port is `5432`

---

### Gmail emails not sending

- Make sure you are using a **Gmail App Password**, not your regular Gmail password
- Go to [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) to generate one
- 2-Step Verification must be enabled on the Gmail account first

---

## Quick Reference — All .env Keys

| Key | Description |
|-----|-------------|
| `MPESA_CONSUMER_KEY` | Daraja app consumer key |
| `MPESA_CONSUMER_SECRET` | Daraja app consumer secret |
| `MPESA_SHORTCODE` | Paybill or till number (174379 for sandbox) |
| `MPESA_PASSKEY` | Lipa Na M-Pesa passkey |
| `MPESA_CALLBACK_URL` | Public HTTPS URL for payment callbacks |
| `MPESA_BASE_URL` | Daraja API base URL (sandbox or production) |
| `DB_HOST` | PostgreSQL host (usually localhost) |
| `DB_NAME` | Database name (mkulima) |
| `DB_USER` | PostgreSQL username |
| `DB_PASSWORD` | PostgreSQL password |
| `DB_PORT` | PostgreSQL port (usually 5432) |
| `GMAIL_ADDRESS` | Gmail address used to send emails |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not your login password) |
