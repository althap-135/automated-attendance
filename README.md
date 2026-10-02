# 🎓 NexAttend AI — Automated Smart Attendance System

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-black.svg)](https://flask.palletsprojects.com/)
[![OpenCV](https://img.shields.io/badge/OpenCV-Computer%20Vision-green.svg)](https://opencv.org/)
[![Twilio](https://img.shields.io/badge/Twilio-WhatsApp%20API-red.svg)](https://www.twilio.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An intelligent, full-stack biometric attendance management platform powered by **OpenCV face recognition**, **Flask**, and **Twilio WhatsApp automation**. Built for schools, colleges, and educational institutions.

---

## 🌟 Key Features

- **📸 Biometric Facial Recognition**: Instant student & faculty attendance marking via camera stream using OpenCV LBPH face recognition.
- **🔄 Two-Step Advisor Review Workflow**:
  1. Attendance is taken and automatically/manually submitted to the Class Advisor as a `Submitted` session.
  2. The assigned Class Advisor logs in, reviews the record, and clicks **Approve & Finalize**.
- **📲 Automated Parent WhatsApp Alerts**:
  - Automatically notifies parents via Twilio WhatsApp Programmable Messaging when their ward is marked absent.
  - Sends review summary notifications to the assigned Class Advisor.
- **⏱️ Background Attendance Scheduler**:
  - Automatically submits draft attendance sessions when class cut-off times expire.
- **📊 Analytics & Insights**:
  - Visual charts showing attendance percentages, department distributions, and historical trends.
- **🔐 Multi-Role Access Control**:
  - Roles for **Super Admin**, **Institutional Admin**, **Class Advisors / Teachers**, and **Students**.
- **🏛️ Department & Section Mapping**:
  - Dynamic mapping of advisors to departments, academic years, and sections.

---

## 🏗️ Architecture & Workflow

```text
    ┌──────────────────────┐
    │  Student / Teacher   │
    └──────────┬───────────┘
               │ (Face Scan / Camera)
               ▼
    ┌──────────────────────┐
    │  Face Recognition    │ ───► Marks Attendance (Draft Session)
    └──────────┬───────────┘
               │ (Manual or Auto-Submit via Scheduler)
               ▼
    ┌──────────────────────┐
    │ Session: Submitted   │ ───► WhatsApp notification sent to Class Advisor
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │ Class Advisor Login  │
    └──────────┬───────────┘
               │ (Approve & Finalize)
               ▼
    ┌──────────────────────┐
    │  Session: Finalized  │ ───► WhatsApp alerts sent to Parents of Absent Students
    └──────────────────────┘
```

---

## 🛠️ Tech Stack

- **Backend**: Python 3.10+, Flask, Flask-SQLAlchemy, Werkzeug, Gunicorn
- **Computer Vision**: OpenCV (`opencv-python-headless`), NumPy, Pillow
- **Database**: SQLite (default) / PostgreSQL compatible via SQLAlchemy
- **Messaging & Notifications**: Twilio Programmable Messaging (WhatsApp API)
- **Frontend**: HTML5, CSS3, JavaScript (Fetch API, WebRTC MediaStream)
- **Deployment**: Render, Gunicorn WSGI

---

## 🚀 Quick Start (Local Setup)

### 1. Clone the Repository
```bash
git clone https://github.com/althap-135/automated-attendance.git
cd automated-attendance
```

### 2. Create and Activate a Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the project root directory:

```ini
# Flask Config
SECRET_KEY=your-super-secret-key
DATABASE_URL=sqlite:///attendance.db

# Twilio WhatsApp Config
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
```

### 5. Run the Application
```bash
python app.py
```
Open your browser and navigate to: **`http://127.0.0.1:5000`**

---

## 🌐 Production Deployment (Render)

This repository includes `render.yaml` and `Procfile` ready for Render deployment.

1. Fork or push this repository to GitHub.
2. Sign in to **[Render](https://render.com)** with your GitHub account.
3. Click **New +** → **Web Service** → Connect your repo.
4. Set the following:
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
5. Under **Environment Variables**, add:
   - `SECRET_KEY`
   - `TWILIO_ACCOUNT_SID`
   - `TWILIO_AUTH_TOKEN`
   - `TWILIO_WHATSAPP_NUMBER`
   - `DATABASE_URL=sqlite:///attendance.db`
6. Click **Deploy Web Service**!

---

## 📁 Project Structure

```text
automated-attendance/
├── app.py                   # Main Flask application & route handlers
├── models.py                # Database models (User, ClassSection, AttendanceSession, etc.)
├── face_service.py          # OpenCV facial recognition and training logic
├── notification_service.py  # Twilio WhatsApp messaging service
├── config.py                # Application configuration & database resolution
├── Procfile                 # Production WSGI process definition
├── render.yaml              # Render deployment blueprint
├── requirements.txt         # Python package dependencies
├── static/                  # Static assets (CSS, JS, trainer files)
│   ├── css/
│   ├── js/
│   └── faces/               # Model trainers and face data
└── templates/               # Jinja2 HTML templates
    ├── base.html
    ├── login.html
    ├── attendance.html
    ├── correction.html      # Advisor review & approval dashboard
    ├── analytics.html
    └── settings.html
```

---

## 🔒 Security & Privacy

- Face images and dataset binaries are not tracked in public version control.
- API keys, credentials, and session secrets are managed strictly through environment variables.
- Password hashes are secured using PBKDF2/Scrypt through Werkzeug.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
