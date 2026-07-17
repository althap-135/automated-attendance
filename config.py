import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Force in-memory database for all test suite runs to protect production/development data
if 'pytest' in sys.modules or 'unittest' in sys.modules or any('pytest' in arg for arg in sys.argv) or os.environ.get('PYTEST_CURRENT_TEST'):
    if not os.environ.get('DATABASE_URL'):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

class Config:
    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'smart-face-attendance-secret-key-9988')
    
    # Base directory
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    
    # Database configuration (Defaults to SQLite, easily switchable to MySQL)
    # To use MySQL: set DATABASE_URL=mysql+pymysql://username:password@localhost/db_name
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', f'sqlite:///{os.path.join(BASE_DIR, "attendance.db")}')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Directories for storing captured face images
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'faces')
    STUDENT_FACES_DIR = os.path.join(UPLOAD_FOLDER, 'students')
    TEACHER_FACES_DIR = os.path.join(UPLOAD_FOLDER, 'teachers')
    
    # LBPH Recognizer model files
    STUDENT_TRAINER_PATH = os.path.join(UPLOAD_FOLDER, 'student_trainer.yml')
    TEACHER_TRAINER_PATH = os.path.join(UPLOAD_FOLDER, 'teacher_trainer.yml')
    
    # Face recognition matching parameters
    # LBPH distance threshold: smaller values mean stricter match. 0 is perfect match.
    # A threshold of 50-80 is typical for LBPH depending on lighting and quality.
    FACE_RECOGNITION_THRESHOLD = float(os.environ.get('FACE_THRESHOLD', 75.0))
    
    # Notification API configuration (Mocks will run unless real values are provided)
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
    TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
    
    # Email settings (SMTP fallback)
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_EMAIL = os.environ.get('SMTP_EMAIL', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')

    @staticmethod
    def init_app(app):
        # Create upload directories if they don't exist
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['STUDENT_FACES_DIR'], exist_ok=True)
        os.makedirs(app.config['TEACHER_FACES_DIR'], exist_ok=True)
