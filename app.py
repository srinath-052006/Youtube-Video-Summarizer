import os
import sqlite3
import random
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, session
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from openai import OpenAI
import yt_dlp
from flask import send_file
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from textwrap import wrap
import io

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ---------------- EMAIL CONFIG ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'ysummarize@gmail.com'
app.config['MAIL_PASSWORD'] = 'vlwouldhpadwkaoy'

mail = Mail(app)

# ---------------- OpenRouter Setup ----------------
client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)

# ---------------- DATABASE ----------------
def init_db():
    with sqlite3.connect("yt_users.db", timeout=10) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                otp TEXT,
                otp_expiry TEXT,
                is_verified INTEGER DEFAULT 0
            )
        """)

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN reset_otp TEXT")
        except:
            pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN reset_expiry TEXT")
        except:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS youtube_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT NOT NULL,
                video_url TEXT NOT NULL,
                summary TEXT,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

init_db()

# ---------------- OTP ----------------
def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp(email, otp):
    msg = Message(
        subject="Your Email Verification OTP",
        sender=app.config['MAIL_USERNAME'],
        recipients=[email]
    )
    msg.body = f"Your OTP code is: {otp}"
    mail.send(msg)

# ---------------- YouTube Metadata ----------------
def get_video_metadata(url):
    ydl_opts = {"quiet": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title", ""),
        "channel": info.get("uploader", ""),
        "description": info.get("description", ""),
        "tags": info.get("tags", []) or []
    }

# ---------------- AI Summary ----------------
def ai_summary_and_concepts(meta):
    prompt = f"""
You are an expert teacher.

Using ONLY the following YouTube video information,
generate a VERY DETAILED explanation as if teaching a student.

REQUIREMENTS:
1. Explain the video content in depth (like a lecture)
2. Break into clear sections with headings
3. Explain each concept in simple language
4. Use examples wherever possible
5. Write a LONG explanation (minimum 400–500 lines if possible)
6. Do NOT summarize shortly – explain fully

Video Information:
Title: {meta['title']}
Channel: {meta['channel']}
Tags: {', '.join(meta['tags'])}
Description: {meta['description']}
"""

    response = client.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You explain topics in extreme detail like a teacher."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.6,
        max_tokens=3500
    )

    return response.choices[0].message.content

# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return redirect(url_for("login"))

# -------- REGISTER --------
@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""

    if request.method == "POST":
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")

        hashed_password = generate_password_hash(password)
        otp = generate_otp()
        hashed_otp = generate_password_hash(otp)
        expiry = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

        with sqlite3.connect("yt_users.db", timeout=10) as conn:
            cursor = conn.cursor()

            # ✅ Check username exists
            cursor.execute("SELECT id FROM users WHERE username=?", (username,))
            if cursor.fetchone():
                error = "Username already exists."
                return render_template("register.html", error=error)

            # ✅ Check email exists
            cursor.execute("SELECT is_verified FROM users WHERE email=?", (email,))
            existing_email = cursor.fetchone()

            if existing_email:
                if existing_email[0] == 1:
                    error = "Email already registered."
                    return render_template("register.html", error=error)
                else:
                    # update unverified user
                    cursor.execute("""
                        UPDATE users
                        SET username=?, password=?, otp=?, otp_expiry=?
                        WHERE email=?
                    """, (username, hashed_password, hashed_otp, expiry, email))
                    conn.commit()

                    send_otp(email, otp)
                    session['verify_email'] = email
                    return redirect(url_for("verify"))

            # ✅ Insert new user
            cursor.execute("""
                INSERT INTO users (username, email, password, otp, otp_expiry)
                VALUES (?, ?, ?, ?, ?)
            """, (username, email, hashed_password, hashed_otp, expiry))
            conn.commit()

        send_otp(email, otp)
        session['verify_email'] = email
        return redirect(url_for("verify"))

    return render_template("register.html", error=error)
# -------- VERIFY OTP --------
@app.route("/verify", methods=["GET", "POST"])
def verify():
    error = ""
    email = session.get('verify_email')

    if not email:
        return redirect(url_for("register"))

    if request.method == "POST":
        entered_otp = request.form.get("otp")

        conn = sqlite3.connect("yt_users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT otp, otp_expiry FROM users WHERE email=?", (email,))
        user = cursor.fetchone()

        if user:
            stored_otp, expiry = user
            expiry_time = datetime.fromisoformat(expiry)

            if datetime.utcnow() > expiry_time:
                error = "OTP expired."
            elif check_password_hash(stored_otp, entered_otp):
                cursor.execute("""
                    UPDATE users SET is_verified=1, otp=NULL WHERE email=?
                """, (email,))
                conn.commit()
                conn.close()
                return redirect(url_for("login"))
            else:
                error = "Invalid OTP."

        conn.close()

    return render_template("verify.html", error=error)

# -------- RESEND OTP --------
@app.route("/resend_otp")
def resend_otp():
    email = session.get('verify_email')
    if not email:
        return redirect(url_for("register"))

    otp = generate_otp()
    hashed_otp = generate_password_hash(otp)
    expiry = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

    conn = sqlite3.connect("yt_users.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET otp=?, otp_expiry=? WHERE email=?
    """, (hashed_otp, expiry, email))
    conn.commit()
    conn.close()

    send_otp(email, otp)
    return redirect(url_for("verify"))

# -------- LOGIN --------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = sqlite3.connect("yt_users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT password, is_verified FROM users WHERE username=?", (username,))
        user = cursor.fetchone()
        conn.close()

        if user:
            stored_password, is_verified = user
            if not is_verified:
                error = "Verify email first."
            elif check_password_hash(stored_password, password):
                session['user'] = username
                return redirect(url_for("dashboard"))
            else:
                error = "Invalid password."
        else:
            error = "User not found."

    return render_template("login.html", error=error)

# -------- DASHBOARD --------

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if 'user' not in session:
        return redirect(url_for("login"))

    video_details = summary = error = ""
    username = session['user']

    if request.method == "POST":
        video_url = request.form.get("url")
        action = request.form.get("action")

        try:
            meta = get_video_metadata(video_url)
            video_details = f"Title: {meta['title']} | Channel: {meta['channel']}"

            if action == "summarize":
                summary = ai_summary_and_concepts(meta)

            conn = sqlite3.connect("yt_users.db")
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO youtube_links (user, video_url, summary)
                VALUES (?, ?, ?)
            """, (username, video_url, summary))
            conn.commit()
            conn.close()

        except Exception as e:
            error = str(e)

    return render_template("dashboard.html",
                           username=username,
                           video_details=video_details,
                           summary=summary,
                           error=error)

# -------- HISTORY --------
@app.route("/history")
def history():
    if 'user' not in session:
        return redirect(url_for("login"))

    user = session['user']
    conn = sqlite3.connect("yt_users.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT video_url, summary, date_added
        FROM youtube_links
        WHERE user=?
        ORDER BY id DESC
    """, (user,))
    links = cursor.fetchall()
    conn.close()

    return render_template("history.html", links=links, user=user)

# -------- LOGOUT --------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

#-------- FORGOT PASSWORD -------
@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    error = ""

    if request.method == "POST":
        email = request.form.get("email")

        with sqlite3.connect("yt_users.db", timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE email=?", (email,))
            user = cursor.fetchone()

            if not user:
                error = "Email not registered."
                return render_template("forgot.html", error=error)

            otp = generate_otp()
            hashed_otp = generate_password_hash(otp)
            expiry = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

            cursor.execute("""
                UPDATE users
                SET reset_otp=?, reset_expiry=?
                WHERE email=?
            """, (hashed_otp, expiry, email))
            conn.commit()

        send_otp(email, otp)
        session['reset_email'] = email
        return redirect(url_for("verify_reset"))

    return render_template("forgot.html", error=error)
# ------ VERIFY RESET ------
@app.route("/verify_reset", methods=["GET", "POST"])
def verify_reset():
    error = ""
    email = session.get("reset_email")

    if not email:
        return redirect(url_for("login"))

    if request.method == "POST":
        entered_otp = request.form.get("otp")

        with sqlite3.connect("yt_users.db", timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT reset_otp, reset_expiry FROM users WHERE email=?", (email,))
            user = cursor.fetchone()

            if user:
                stored_otp, expiry = user
                expiry_time = datetime.fromisoformat(expiry)

                if datetime.utcnow() > expiry_time:
                    error = "OTP expired."
                elif check_password_hash(stored_otp, entered_otp):
                    return redirect(url_for("reset_password"))
                else:
                    error = "Invalid OTP."

    return render_template("verify_reset.html", error=error)
# --- RESEND OTP ---
@app.route("/resend_reset_otp")
def resend_reset_otp():
    email = session.get("reset_email")
    if not email:
        return redirect(url_for("login"))

    otp = generate_otp()
    hashed_otp = generate_password_hash(otp)
    expiry = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

    with sqlite3.connect("yt_users.db", timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET reset_otp=?, reset_expiry=?
            WHERE email=?
        """, (hashed_otp, expiry, email))
        conn.commit()

    send_otp(email, otp)
    return redirect(url_for("verify_reset"))

# --- RESET PASSWORD ---
@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    error = ""
    email = session.get("reset_email")

    if not email:
        return redirect(url_for("login"))

    if request.method == "POST":
        new_password = request.form.get("password")
        hashed_password = generate_password_hash(new_password)

        with sqlite3.connect("yt_users.db", timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users
                SET password=?, reset_otp=NULL, reset_expiry=NULL
                WHERE email=?
            """, (hashed_password, email))
            conn.commit()

        session.pop("reset_email", None)
        return redirect(url_for("login"))

    return render_template("reset_password.html", error=error)

#-------change username-----
@app.route("/change_username", methods=["GET","POST"])
def change_username():

    if 'user' not in session:
        return redirect(url_for("login"))

    message=""
    current_user=session['user']

    if request.method=="POST":

        new_username=request.form.get("new_username")

        conn=sqlite3.connect("yt_users.db")
        cursor=conn.cursor()

        cursor.execute("UPDATE users SET username=? WHERE username=?",
                       (new_username,current_user))

        conn.commit()
        conn.close()

        session['user']=new_username
        message="Username updated successfully!"

    return render_template("change_username.html",message=message)

#---------Delete account-----
@app.route("/delete_account", methods=["GET","POST"])
def delete_account():

    if 'user' not in session:
        return redirect(url_for("login"))

    username=session['user']

    if request.method=="POST":

        conn=sqlite3.connect("yt_users.db")
        cursor=conn.cursor()

        cursor.execute("DELETE FROM users WHERE username=?", (username,))
        cursor.execute("DELETE FROM youtube_links WHERE user=?", (username,))

        conn.commit()
        conn.close()

        session.clear()

        return redirect(url_for("register"))

    return render_template("delete_account.html")

#------download pdf-------
@app.route("/download_pdf", methods=["POST"])
def download_pdf():

    title = request.form.get("title")
    summary = request.form.get("summary")

    buffer = io.BytesIO()

    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    y = height - 50

    # Title
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, "YouTube Video Summary")
    y -= 40

    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, title)
    y -= 30

    p.setFont("Helvetica", 11)

    # 🔥 wrap summary text properly
    lines = summary.split("\n")

    for line in lines:

        wrapped_lines = wrap(line, 90)   # line width control

        for wrap_line in wrapped_lines:

            if y < 50:
                p.showPage()
                p.setFont("Helvetica", 11)
                y = height - 50

            p.drawString(50, y, wrap_line)
            y -= 18

    p.save()

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="video_summary.pdf",
        mimetype="application/pdf"
    )

if __name__ == "__main__":
    app.run(debug=True)