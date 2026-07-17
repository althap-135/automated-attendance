import base64
import os
from datetime import datetime, date, timedelta
import numpy as np
import cv2
from werkzeug.security import generate_password_hash, check_password_hash

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from config import Config
from models import db, User, Attendance, AuditLog, NotificationLog
from face_service import FaceService
from notification_service import NotificationService
import json
import requests

app = Flask(__name__)
app.config.from_object(Config)

# AI Configuration helpers
CONFIG_FILE = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config.json')

def load_ai_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                return data
        except Exception as e:
            print(f"Exception loading CONFIG_FILE: {e}", flush=True)
    env_key = os.environ.get('OPENAI_API_KEY', '')
    return {'provider': 'openai', 'api_key': env_key}


def save_ai_config(provider, api_key):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump({'provider': provider, 'api_key': api_key}, f)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False


# Initialize database
db.init_app(app)
Config.init_app(app)

# Helper function to decode base64 images from browser
def decode_base64_image(base64_str):
    try:
        if ',' in base64_str:
            base64_str = base64_str.split(',')[1]
        img_data = base64.b64decode(base64_str)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"Error decoding base64 image: {e}")
        return None


# Context processor to make login state available to all templates
@app.context_processor
def inject_user():
    user = None
    if session.get('logged_in') and session.get('user_id'):
        user = User.query.get(session.get('user_id'))
    return {
        'current_user': user,
        'current_teacher_name': user.name if user else None,
        'current_teacher_id': user.email if user else None,
        'is_logged_in': session.get('logged_in', False)
    }

def normalize_section(val):
    if not val:
        return ""
    val = val.strip().lower()
    val = val.replace("section", "").replace("sec", "").replace("-", "").replace("_", "").strip()
    return val

def normalize_year(val):
    if not val:
        return ""
    val = val.strip().lower()
    # Map common variations
    year_map = {
        '1st year': '1', '1st': '1', 'first year': '1', 'first': '1', 'i year': '1', 'i': '1', '1': '1',
        '2nd year': '2', '2nd': '2', 'second year': '2', 'second': '2', 'ii year': '2', 'ii': '2', '2': '2',
        '3rd year': '3', '3rd': '3', 'third year': '3', 'third': '3', 'iii year': '3', 'iii': '3', '3': '3',
        '4th year': '4', '4th': '4', 'fourth year': '4', 'fourth': '4', 'iv year': '4', 'iv': '4', '4': '4',
        '10th': '10', '10': '10', 'tenth': '10', '10th class': '10', '10th grade': '10'
    }
    return year_map.get(val, val)

def normalize_dept(val):
    if not val:
        return ""
    val = val.strip().lower()
    # Map common variations
    dept_map = {
        'computer science': 'cse',
        'computer science engineering': 'cse',
        'computer science and engineering': 'cse',
        'cse': 'cse',
        'electronics': 'ece',
        'electronics and communication': 'ece',
        'electronics and communication engineering': 'ece',
        'ece': 'ece',
        'information technology': 'it',
        'it': 'it',
        'mechanical': 'mech',
        'mech': 'mech',
        'mechanical engineering': 'mech',
        'electrical and electronics': 'eee',
        'eee': 'eee',
        'electrical and electronics engineering': 'eee'
    }
    return dept_map.get(val, val)

def normalize_institution(val):
    if not val:
        return ""
    val = val.strip().lower()
    val = val.replace("college", "").replace("school", "").replace("university", "").replace("of engineering", "").replace("engineering", "").replace("-", "").replace(" ", "").strip()
    return val

    user_id = session.get('user_id')
    if not user_id:
        return None
    from models import User
    return User.query.get(user_id)

def resolve_authorized_class_sections(user):
    """
    Returns a list of ClassSection records that the user is authorized to access.
    """
    if not user:
        return []
        
    from models import ClassSection
    
    # Restrict by institution_id
    if not user.institution_id:
        return []
        
    query = ClassSection.query.filter_by(institution_id=user.institution_id)
    
    # If the user is a teacher/advisor, they only see ClassSections where they are advisor
    if user.role in ['teacher', 'advisor']:
        query = query.filter_by(advisor_id=user.id)
        
    return query.all()

def resolve_active_class_section(user):
    """
    Resolves the active ClassSection for the user (checking session or defaulting).
    Enforces authorization to prevent IDOR.
    """
    if not user:
        return None
        
    authorized_classes = resolve_authorized_class_sections(user)
    
    # Fallback to resolver if they have no authorized classes yet (e.g. newly created advisor)
    if not authorized_classes and user.role in ['teacher', 'advisor']:
        # This resolves and auto-creates the ClassSection safely
        cs = resolve_current_class_section(user)
        if cs:
            return cs
            
    if not authorized_classes:
        return None
        
    # Check if they have an active class selected in session
    active_class_id = session.get('active_class_section_id')
    if active_class_id:
        # Find in authorized list to prevent IDOR
        for cs in authorized_classes:
            if cs.id == int(active_class_id):
                return cs
                
    # Default to the first authorized ClassSection
    return authorized_classes[0]

def verify_class_access(user, class_section):
    """
    Verifies that the user has permission to access the given ClassSection.
    """
    if not user or not class_section:
        return False
        
    # Must belong to the same institution
    if user.institution_id != class_section.institution_id:
        return False
        
    # If teacher/advisor, they must be the advisor
    if user.role in ['teacher', 'advisor']:
        return class_section.advisor_id == user.id
    session['user_role'] = new_user.role
    
    # Compatibility session keys for legacy codes
    session['teacher_name'] = new_user.name
    session['teacher_id'] = new_user.email
    
    return jsonify({'success': True, 'message': 'Registration successful!'})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json() or request.form
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'success': False, 'message': 'Email and Password are required.'}), 400
        
    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password, password):
        return jsonify({'success': False, 'message': 'Invalid Email or Password.'}), 401
        
    # Set session keys
    session['logged_in'] = True
    session['user_id'] = user.id
    session['user_role'] = user.role
    
    # Compatibility keys
    session['teacher_name'] = user.name
    session['teacher_id'] = user.email
    
    candidates = User.query.filter(
        User.role == 'student',
        func.lower(func.trim(User.institution_name)) == inst_normalized
    ).all()
    
@app.route('/')
def home():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    # Filter students in the same institution, class, and section
    students_query = User.query.filter_by(
        role='student',
        institution_name=user.institution_name,
        department_or_class=user.department_or_class,
        section=user.section
    )
    total_students = students_query.count()
    student_ids = [s.id for s in students_query.all()]
    
    today = date.today()
    session_status = "Draft"
    if user and user.role == 'teacher':
        daily_sess = get_or_create_daily_session(user.department_or_class, user.year, user.section, today)
        session_status = daily_sess.status

    if student_ids:
        today_present = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Present').count()
        today_absent = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Absent').count()
        recorded_ids = [a.user_id for a in Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today).all()]
        unmarked_count = len(set(student_ids) - set(recorded_ids))
    else:
        today_present = 0
        today_absent = 0
        unmarked_count = 0
        
    effective_absent = today_absent + unmarked_count
    
    attendance_rate = 0.0
    if total_students > 0:
        attendance_rate = (today_present / total_students) * 100
        
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(5).all()
    
    return render_template(
        'home.html',
        total_students=total_students,
        today_present=today_present,
        today_absent=effective_absent,
        unmarked_count=unmarked_count,
        attendance_rate=round(attendance_rate, 1),
        recent_logs=recent_logs,
        session_status=session_status
    )

        
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(5).all()
    
    return render_template(
        'home.html',
        total_students=total_students,
        today_present=today_present,
        today_absent=effective_absent,
        unmarked_count=unmarked_count,
        attendance_rate=round(attendance_rate, 1),
        recent_logs=recent_logs,
        session_status=session_status,
        present_students=present_students
    )

    from models import User
    
    # Query strictly by class_section_id
    students = User.query.filter_by(
        role='student',
        class_section_id=class_section.id
    ).all()
            
    return students

def is_setup_completed(institution_id):
    if app.config.get('TESTING'):
        return True
    if not institution_id:
        return False
    if user.role == 'teacher' and not session.get('teacher_face_verified'):
        return redirect(url_for('teacher_auth_page'))
        
    today = date.today()
    session_status = "Draft"
# REPAIRED:         return "Access denied. Only teachers or admins can access this page.", 403
        
    if user.role == 'teacher' and not session.get('teacher_face_verified'):
        return redirect(url_for('teacher_auth_page'))
        
    today = date.today()
    session_status = "Draft"
    if user.role == 'teacher':
        daily_sess = get_or_create_daily_session(user.department_or_class, user.year, user.section, today)
        session_status = daily_sess.status

    students = User.query.filter_by(
        role='student',
        institution_name=user.institution_name,
        department_or_class=user.department_or_class,
        section=user.section
    ).all()
    
    attendance_map = {a.user_id: a for a in Attendance.query.filter_by(date=today).all()}
# REPAIRED:     ).all()
    advisors_mapped_ids = [cs.advisor_id for cs in class_sections if cs.advisor_id]
    advisors_mapped = User.query.filter(User.id.in_(advisors_mapped_ids)).all() if advisors_mapped_ids else []
    advisors = list({a.id: a for a in (advisors_by_dept + advisors_mapped)}.values())
    if not advisors:
        return False
        
    # Advisor face (all advisors must have face registered)
    if not all(a.face_registered for a in advisors):
        return False
        
    if len(students_with_face) != len(students):
        return False
        
    # Timing configured (at least one class section has non-default time)
    default_start = '09:00'
    default_end = '09:30'
    timing_configured = any(
        (cs.start_time.strftime('%H:%M') != default_start or cs.end_time.strftime('%H:%M') != default_end)
        for cs in class_sections if cs.start_time and cs.end_time
    )
# ----------------- AUTHENTICATION ROUTES -----------------

@app.route('/login')
def login():
    if session.get('logged_in'):
        return redirect(url_for('home'))
    return render_template('login.html', page_type='login')

@app.route('/register')
def register():
    # Specific API paths allowed without session
    allowed_apis = ['/api/attendance/scan', '/api/settings/seed', '/api/setup/']
    
    if request.endpoint in allowed_routes or any(request.path.startswith(api) for api in allowed_apis):
        return
        
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': 'Authentication required.'}), 401
        return redirect(url_for('login'))
    allowed_routes = ['login', 'register', 'api_login', 'api_register', 'static', 'setup_wizard']
    # Specific API paths allowed without session
    allowed_apis = ['/api/attendance/scan', '/api/settings/seed', '/api/setup/']
    
    if request.endpoint in allowed_routes or any(request.path.startswith(api) for api in allowed_apis):
# REPAIRED:     if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': 'Authentication required.'}), 401
        return redirect(url_for('login'))


@app.route('/analytics')
def analytics_page():
    return render_template('analytics.html')

@app.route('/settings')
def settings_page():
    students = User.query.filter_by(role='student').all()
    teachers = User.query.filter_by(role='teacher').all()
    notification_logs = NotificationLog.query.order_by(NotificationLog.timestamp.desc()).all()
# REPAIRED:             if user.role == 'admin':
# REPAIRED:                 return redirect(url_for('setup_wizard'))
# REPAIRED:             else:
# REPAIRED:                 if request.path not in ['/', '/attendance']:
# REPAIRED:                     return render_template('locked_system.html'), 403

def get_or_create_daily_session(dept, year, section, target_date=None):
    if not target_date:
        target_date = date.today()
    dept = dept or ""
    year = year or ""
    section = section or ""
    from models import AttendanceSession
    sess_rec = AttendanceSession.query.filter_by(
        department=dept,
        year=year,
        department=dept,
        year=year,
        section=section,
        attendance_date=target_date
    ).first()
    if not sess_rec:
        sess_rec = AttendanceSession(
            department=dept,
            year=year,
            section=section,
# REPAIRED:     students_query = User.query.filter_by(**query_args)
# REPAIRED:     total_students = students_query.count()
# REPAIRED:     student_ids = [s.id for s in students_query.all()]
    
# REPAIRED:     today = date.today()
# REPAIRED:     session_status = "Draft"
# REPAIRED:     if user and user.role == 'teacher':
# REPAIRED:         daily_sess = get_or_create_daily_session(user.department_or_class, user.year, user.section, today)
# REPAIRED:         session_status = daily_sess.status

# REPAIRED:     present_students = []
# REPAIRED:     if student_ids:
# REPAIRED:         today_present = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Present').count()
# REPAIRED:         today_absent = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Absent').count()
    
# REPAIRED:     return jsonify({'success': True, 'message': 'Registration successful!'})

# REPAIRED: @app.route('/api/login', methods=['POST'])
# REPAIRED: def api_login():
# REPAIRED:     data = request.get_json() or request.form
# REPAIRED:     email = data.get('email', '').strip()
# REPAIRED:     password = data.get('password', '')
# REPAIRED: def api_register():
# REPAIRED:     data = request.get_json() or request.form
# REPAIRED:         return jsonify({'success': False, 'message': 'Email and Password are required.'}), 400
        
# REPAIRED:     user = User.query.filter_by(email=email).first()
# REPAIRED:     if not user or not check_password_hash(user.password, password):
# REPAIRED:         return jsonify({'success': False, 'message': 'Invalid Email or Password.'}), 401
        
    # Clear any previous session residue
# REPAIRED:     session.clear()
    
    # Check/bind institution_id if NULL
# REPAIRED:     if not user.institution_id and user.institution_name:
# REPAIRED:         from models import Institution
# REPAIRED:         inst = Institution.query.filter_by(name=user.institution_name.strip()).first()
# REPAIRED:         if not inst:
# REPAIRED:             inst = Institution(name=user.institution_name.strip(), institution_type=user.institution_type or "College")
# REPAIRED:             db.session.add(inst)
# REPAIRED:             db.session.commit()
# REPAIRED:         user.institution_id = inst.id
# REPAIRED:         db.session.commit()
        
    # Set session keys
# REPAIRED:     session['logged_in'] = True
# REPAIRED:     session['user_id'] = user.id
# REPAIRED:     session['user_role'] = user.role
# REPAIRED:     session['institution_id'] = user.institution_id
    
    # Compatibility keys
# REPAIRED:     session['teacher_name'] = user.name
# REPAIRED:     session['teacher_id'] = user.email
        unmarked_count=unmarked_count,
    # Auto-resolve and set active class section
# REPAIRED:     cs = resolve_active_class_section(user)
# REPAIRED:     if cs:
# REPAIRED:         session['active_class_section_id'] = cs.id
        
# REPAIRED:     return jsonify({'success': True, 'message': f'Welcome back, {user.name}!'})


# ----------------- PAGE ROUTES -----------------

# REPAIRED: @app.route('/')
# REPAIRED: def home():
# REPAIRED:     user_id = session.get('user_id')
# REPAIRED:     if not user_id:
# REPAIRED:         return redirect(url_for('login'))
        
# REPAIRED:     user = User.query.get(user_id)
# REPAIRED:     if not user:
# REPAIRED:         session.clear()
# REPAIRED:         return redirect(url_for('login'))
    
    # 1. Resolve Active ClassSection
# REPAIRED:     cs = resolve_active_class_section(user)
    
# REPAIRED:     if cs:
# REPAIRED:         matched_students = get_students_for_class(user, cs)
# REPAIRED:         total_students = len(matched_students)
# REPAIRED:         student_ids = [s.id for s in matched_students]
# REPAIRED:     else:
# REPAIRED:         matched_students = []
# REPAIRED:         total_students = 0
# REPAIRED:         student_ids = []
        
# REPAIRED:     today = date.today()
# REPAIRED:     session_status = "Draft"
# REPAIRED:     if cs:
# REPAIRED:         daily_sess = get_or_create_daily_session(cs.department, cs.year or "", cs.section, today)
# REPAIRED: def api_login():
# REPAIRED:     data = request.get_json() or request.form
# REPAIRED:     email = data.get('email', '').strip()
# REPAIRED:         if user.year:
# REPAIRED:         present_attendance = Attendance.query.filter(
            Attendance.user_id.in_(student_ids),
            Attendance.date == today,
# REPAIRED:             Attendance.status == 'Present'
# REPAIRED:         ).all()
# REPAIRED:         seen_student_ids = set()
# REPAIRED:         for att in present_attendance:
# REPAIRED:             if att.user_id not in seen_student_ids:
# REPAIRED:                 seen_student_ids.add(att.user_id)
# REPAIRED:                 if att.user:
# REPAIRED:                     present_students.append(att.user)
# REPAIRED:     else:
# REPAIRED:         today_present = 0
# REPAIRED:         today_absent = 0
# REPAIRED:         unmarked_count = 0
        
# REPAIRED:     effective_absent = today_absent + unmarked_count
    
# REPAIRED:     attendance_rate = 0.0
# REPAIRED:     if total_students > 0:
        attendance_rate = (today_present / total_students) * 100
        
    # Calculate Overall Rate (historical average of the ClassSection)
    overall_rate = 0.0
    if student_ids:
        all_records = Attendance.query.filter(Attendance.user_id.in_(student_ids)).all()
        total_records = len(all_records)
        if total_records > 0:
            present_records = sum(1 for r in all_records if r.status.strip().capitalize() == 'Present')
            overall_rate = (present_records / total_records) * 100
            
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(5).all()
    from models import ClassSection
    class_sections_list = ClassSection.query.all() if user.role == 'admin' else []


# ----------------- PAGE ROUTES -----------------

@app.route('/')
        student_reg_numbers = [s.reg_number for s in matched_students if s.reg_number]
        if student_reg_numbers:
            recent_logs = AuditLog.query.filter(AuditLog.register_number.in_(student_reg_numbers)).order_by(AuditLog.timestamp.desc()).limit(5).all()
            
    authorized_classes = resolve_authorized_class_sections(user)
    class_sections_list = authorized_classes if (len(authorized_classes) > 1 or user.role == 'admin') else []
    
    return render_template(
        'home.html',
        total_students=total_students,
    if not user:
        session.clear()
        return redirect(url_for('login'))
        
    # SETUP WIZARD GUARD: Redirect admin if setup is incomplete; render lock screen for teacher
    if not is_setup_completed(user.institution_id):
        if user.role == 'admin':
            return redirect(url_for('setup_wizard'))
        else:
            return render_template('locked_system.html')
    )

@app.route('/attendance')
@app.route('/settings')
def settings_page():
    students = User.query.filter_by(role='student').all()
    if cs:
        daily_sess = get_or_create_daily_session(cs.department, cs.year or "", cs.section, today)
        session_status = daily_sess.status

    present_students = []
    if student_ids:
        today_present = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Present').count()
        today_absent = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Absent').count()
        recorded_ids = [a.user_id for a in Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today).all()]
        unmarked_count = len(set(student_ids) - set(recorded_ids))
        
        # Query present students details without duplicates
        present_attendance = Attendance.query.filter(
            Attendance.user_id.in_(student_ids),
    return render_template('attendance.html')

@app.route('/teacher/auth')
def teacher_auth_page():
    return render_template('teacher_auth.html')

@app.route('/teacher/dashboard')
def teacher_dashboard():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    if not user or user.role not in ['teacher', 'admin']:
        return "Access denied. Only teachers or admins can access this page.", 403
        
    if user.role == 'teacher' and not session.get('teacher_face_verified'):
        return redirect(url_for('teacher_auth_page'))
        
    today = date.today()
    session_status = "Draft"
    
    # Resolve Active ClassSection
    cs = resolve_active_class_section(user)
    if cs:
        daily_sess = get_or_create_daily_session(cs.department, cs.year or "", cs.section, today)
        session_status = daily_sess.status
        students = get_students_for_class(user, cs)
    else:
        students = []
    
    student_ids = [s.id for s in students]
    attendance_map = {a.user_id: a for a in Attendance.query.filter(Attendance.date==today, Attendance.user_id.in_(student_ids)).all() if student_ids}
@app.route('/api/attendance/scan', methods=['POST'])
        student_reg_numbers = [s.reg_number for s in matched_students if s.reg_number]
        if student_reg_numbers:
            recent_logs = AuditLog.query.filter(AuditLog.register_number.in_(student_reg_numbers)).order_by(AuditLog.timestamp.desc()).limit(5).all()
            
    authorized_classes = resolve_authorized_class_sections(user)
    class_sections_list = authorized_classes if (len(authorized_classes) > 1 or user.role == 'admin') else []
    
    return render_template(
        'home.html',
        current_class_section=cs,
        class_sections_list=class_sections_list
    )

@app.route('/attendance')
        
    student = User.query.filter_by(role='student', email=register_number).first_or_404()
    
    # Prevent IDOR: check student class context against advisor/institution scope
    if not student.class_section_id:
        
    # Get student's history
    history_records = Attendance.query.filter_by(user_id=student.id).order_by(Attendance.date.desc()).all()
    
    return render_template(
@app.route('/api/attendance/submit_review', methods=['POST'])
def api_submit_review():
    """
    Teacher submits daily attendance to Class Advisor for review.
    Marks all unmarked students as Absent.
        session_status=session_status,
        present_students=present_students,
        current_class_section=cs,
        class_sections_list=class_sections_list
    )

@app.route('/attendance')
def attendance_page():
    return render_template('attendance.html')

@app.route('/teacher/auth')
def teacher_auth_page():
    return render_template('teacher_auth.html')

@app.route('/teacher/dashboard')
def teacher_dashboard():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    if not user or user.role not in ['teacher', 'admin']:
        return "Access denied. Only teachers or admins can access this page.", 403
        
    if user.role == 'teacher' and not session.get('teacher_face_verified'):
        return redirect(url_for('teacher_auth_page'))
        
    today = date.today()
    session_status = "Draft"
    
    # Resolve Active ClassSection
    cs = resolve_active_class_section(user)
    if cs:
        daily_sess = get_or_create_daily_session(cs.department, cs.year or "", cs.section, today)
        session_status = daily_sess.status
        students = get_students_for_class(user, cs)
    else:
        students = []
    
    # State validation
    if sess_rec.status != 'Draft':
        return jsonify({'success': False, 'message': f'Cannot submit review. Session is currently in {sess_rec.status} status.'}), 400
        
def analytics_page():
    return render_template('analytics.html')

@app.route('/settings')
def settings_page():
    user = User.query.get(session.get('user_id'))
    if not user:
        return redirect(url_for('login'))
        
    cs = resolve_active_class_section(user)
    
        total_users=total_users,
        faces_registered=faces_registered,
        pending_registration=pending_registration,
        total_students=total_students,
        total_teachers=total_teachers,
        resolved_dept=resolved_dept,
        resolved_year=resolved_year,
        resolved_sec=resolved_sec
    )

            students = get_students_for_class(user, cs)
            class_sections = [cs]
            notification_logs = NotificationLog.query.filter_by(class_section_id=cs.id).order_by(NotificationLog.timestamp.desc()).all()
        else:
            students = []
            class_sections = []
            notification_logs = []
        teachers = []
        
    # Load current AI configuration
    ai_config = load_ai_config()
    raw_key = ai_config.get('api_key', '')
    masked_key = ''
    if raw_key:
        masked_key = raw_key[:6] + '*' * max(0, len(raw_key) - 10) + raw_key[-4:] if len(raw_key) > 10 else '******'
        
    resolved_dept = cs.department if cs else ""
    resolved_year = cs.year or ""
    resolved_sec = cs.section if cs else ""
    
    total_students = len(students)
    total_teachers = len(teachers)
    total_users = total_students + total_teachers
    faces_registered = sum(1 for s in students if s.face_registered) + sum(1 for t in teachers if t.face_registered)
        attendance_date=target_date
    ).first()
    
    if not sess_rec:
        return jsonify({'success': False, 'message': 'No attendance session found for today.'}), 404
        
    if sess_rec.status == 'Finalized':
        return jsonify({'success': False, 'message': 'Attendance has already been approved and finalized.'}), 400
        
    if sess_rec.status != 'Submitted':
        return jsonify({'success': False, 'message': 'Attendance must be submitted for review before final approval.'}), 400
        
    # Retrieve students
    students = User.query.filter_by(
        role='student',
        department_or_class=dept,
        year=year,
        section=section
    ).all()
    
    # Get attendance mapping

@app.route('/api/attendance/scan', methods=['POST'])
def api_scan_face():
    data = request.get_json()
        ai_key=masked_key,
        total_users=total_users,
        faces_registered=faces_registered,
        pending_registration=pending_registration,
        total_students=total_students,
    
    if img_bgr is None:
        return jsonify({'success': False, 'message': 'Failed to decode image.'}), 400
        
    predictions = FaceService.predict_face(img_bgr, user_type)
        'stats': {
            'total': total_students,
            'present': present_count,
            'absent': absent_count
        }
    })

@app.route('/api/attendance/approve_finalize', methods=['POST'])
def api_approve_finalize():
    """
    best_pred = predictions[0]
    
    if best_pred['confidence'] <= threshold:
        match = best_pred
        
    if not match:
        return jsonify({
            'success': False, 
            'message': 'Face detected but not recognized.',
            'confidence': best_pred['confidence'],
    # Process predictions, look for a match below the threshold
    match = None
    threshold = app.config['FACE_RECOGNITION_THRESHOLD']
    
    # Sort by confidence (LBPH distance - lower is better)
    predictions = sorted(predictions, key=lambda x: x['confidence'])
    best_pred = predictions[0]
    
    if best_pred['confidence'] <= threshold:
        match = best_pred
        
    if not match:
        return jsonify({
            'success': False, 
            'message': 'Face detected but not recognized.',
            'confidence': best_pred['confidence'],
            'box': best_pred['box']
        })
        
    # We found a match! Let's record in DB
    matched_id = match['id']
    conf = match['confidence']
    box = match['box']
    
    if user_type == 'student':
        student = User.query.filter_by(id=matched_id, role='student').first()
        if not student:
            return jsonify({'success': False, 'message': 'Student record not found in database.'})
            
        # Enforce cross-class scanner isolation
        creator_id = session.get('user_id')
        creator = User.query.get(creator_id) if creator_id else None
        if creator:
            cs = resolve_active_class_section(creator)
            if not cs or student.class_section_id != cs.id:
                return jsonify({'success': False, 'message': 'Cross-class scanning rejected: Student belongs to another class.'}), 403
            
        today = date.today()
        now_time = datetime.now().time()
        
            'absent': len(absent_students)
        }
# REPAIRED:     })


        'message': f"Attendance status corrected to Present for {student.name}."
# REPAIRED:     })


@app.route('/api/attendance/submit_review', methods=['POST'])
        'message': f"Attendance approved & finalized. Parent WhatsApp alerts triggered for {len(parent_logs)} absentees.",
        'stats': {
            'total': len(students),
            'present': len(students) - len(absent_students),
            'absent': len(absent_students)
        }
# REPAIRED:     })


@app.route('/api/attendance/today_present', methods=['GET'])
                    'box': box
# REPAIRED:                 })
            else:
                # If marked absent, update to present
                existing.status = 'Present'
    if not user:
        return jsonify({'success': False, 'message': 'User not found'}), 404
        
    students_query = User.query.filter_by(
        role='student',
        
        return jsonify({
            'success': True,
            'message': f"Attendance marked for {student.name}.",
            'student': student.to_dict(),
            'confidence': conf,
            'box': box
        })
        
    elif user_type == 'teacher':
            time=now_time,
            status='Present',
            marked_by='Face Recognition'
        )
        db.session.add(new_attendance)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Attendance marked for {student.name}.",
            'student': student.to_dict(),
            'confidence': conf,
            'box': box
        })
        
    elif user_type == 'teacher':
        teacher = User.query.filter_by(id=matched_id, role='teacher').first()
        if not teacher:
            return jsonify({'success': False, 'message': 'Teacher record not found in database.'})
            
            parent_msg = (
                f"Dear Parent,\n\n"
                f"Your ward {s.name} ({s.reg_number or 'N/A'}) has been marked Absent today.\n\n"
                f"Date: {target_date.strftime('%d-%m-%Y')}\n"
                f"Class: {dept} {year} - {section}\n\n"
                f"Please contact the department if clarification is required.\n\n"
                f"Smart AI Attendance System"
            )
            p_log = NotificationLog(
                class_id=f"{dept} {year} - {section}",
        return jsonify({'success': False, 'message': 'Access denied. Successful face recognition scan required.'}), 403
        
    data = request.get_json()
    if not data or 'student_id' not in data or 'date' not in data or 'new_status' not in data or 'reason' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    student_id = data['student_id']
    date_str = data['date'] # YYYY-MM-DD
    new_status = data['new_status'] # "Present" or "Absent"
    reason = data['reason']
def api_correct_attendance():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401
        
    user = User.query.get(session.get('user_id'))
    if not user or (user.role == 'teacher' and not session.get('teacher_face_verified')):
    if not dept or not section:
        return jsonify({'success': False, 'message': 'Class details are required.'}), 400
        
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400
        
    from models import AttendanceSession
        return jsonify({'success': False, 'message': 'Message cannot be empty.'}), 400
        
    # Gather Database Context
    today = date.today()
    total_students = User.query.filter_by(role='student').count()
    today_present = Attendance.query.filter_by(date=today, status='Present').count()
    today_absent = Attendance.query.filter_by(date=today, status='Absent').count()
    
    recorded_ids = [a.user_id for a in Attendance.query.filter_by(date=today).all()]
    unmarked_count = User.query.filter(User.role == 'student', ~User.id.in_(recorded_ids)).count() if recorded_ids else total_students
    effective_absent = today_absent + unmarked_count
    
    attendance_rate = 0.0
    if total_students > 0:
        attendance_rate = round((today_present / total_students) * 100, 1)
        
    # Get compact student list context
    students = User.query.filter_by(role='student').all()
    students_list = []
    for s in students:
        students_list.append(f"- Name: {s.name}, Reg: {s.email}, Dept: {s.department_or_class}, Year: {s.year or 'N/A'}")
    students_context = "\n".join(students_list) if students_list else "No students registered yet."
    
    # Get compact today's attendance logs
    today_records = Attendance.query.filter_by(date=today).all()
        record.marked_by = f"Teacher: {session.get('teacher_name')} ({session.get('teacher_id')})"
    else:
        # Create new
        record = Attendance(
            student_id=student_id,
            date=record_date,
            time=now_time,
            status='Present',
            marked_by=f"Teacher: {session.get('teacher_name')} ({session.get('teacher_id')})"
        )
        db.session.add(record)
        
    # Create Audit Log
    audit = AuditLog(
        student_name=student.name,
        register_number=student.register_number,
        teacher_name=session.get('teacher_name'),
        teacher_id=session.get('teacher_id'),
        previous_status=prev_status,
        updated_status='Present',
        reason=reason
    )
    db.session.add(audit)
    db.session.commit()
    
    # Auto-logout teacher after successful correction
    session.clear()
    
    return jsonify({
        'success': True,
        'message': f"Attendance status corrected to Present for {student.name}."
    })


@app.route('/api/attendance/submit_review', methods=['POST'])
def api_submit_review():
    """
    Teacher submits daily attendance to Class Advisor for review.
    Marks all unmarked students as Absent.
    Sends one WhatsApp summary message to the Class Advisor.


@app.route('/api/attendance/submit_review', methods=['POST'])
def api_submit_review():
    """
    Teacher submits daily attendance to Class Advisor for review.
    Marks all unmarked students as Absent.
    year = data.get('year', '').strip()
    section = data.get('section', '').strip()
    
    session.clear()
    
    return jsonify({
        'success': True,
        'message': f"Attendance status corrected to Present for {student.name}."
    })


@app.route('/api/attendance/submit_review', methods=['POST'])
def api_submit_review():
    """
    Teacher submits daily attendance to Class Advisor for review.
    Marks all unmarked students as Absent.
    Sends one WhatsApp summary message to the Class Advisor.
    """
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401
    
    # Staff Face Auth is required for this protected operation
    if not session.get('teacher_face_verified'):
# REPAIRED:     })





@app.route('/api/student/search', methods=['GET'])
def api_search_students():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
        
    # Search by register number or name
    results = User.query.filter(User.role == 'student').filter(
        (User.email.like(f"%{query}%")) | 
        (User.name.like(f"%{query}%"))
    if user.role in ['teacher', 'advisor'] or (user.department_or_class or user.year or user.section):
        if user.department_or_class:
            query_args['department_or_class'] = user.department_or_class
        if user.year:
            query_args['year'] = user.year
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401
        
    data = request.get_json() or {}
    date_str = data.get('date', date.today().strftime('%Y-%m-%d'))
    dept = data.get('department', '').strip()
    year = data.get('year', '').strip()
    section = data.get('section', '').strip()
    
    # Resolve from active session
        institution_id=teacher_record.institution_id,
        department=dept,
        year=year,
        section=section
    ).first()
    
    if mapping:
        if not verify_class_access(teacher_record, mapping):
            return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to submit review for this class section.'}), 403
    else:
        is_own_class = (
            dept.lower() == (teacher_record.department_or_class or '').lower() and
            section.lower() == (teacher_record.section or '').lower()
        )
        if not is_own_class:
            return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to submit review for this class section.'}), 403
        
    # Get or lazy create session
    sess_rec = get_or_create_daily_session(dept, year, section, target_date)
    # Ensure session class_section_id is set
    if mapping and not sess_rec.class_section_id:
                        'face_registered': st.face_registered
# REPAIRED:                     })
    else:
        today_present = 0

@app.route('/api/attendance/submit_review', methods=['POST'])
def api_submit_review():
    """
    Teacher submits daily attendance to Class Advisor for review.
    Marks all unmarked students as Absent.
    Sends one WhatsApp summary message to the Class Advisor.
    """
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401
    
    # Staff Face Auth is required for this protected operation
    user_role = session.get('user_role')
    if user_role == 'teacher' and not session.get('teacher_face_verified'):
        return jsonify({'success': False, 'message': 'Staff Face Authentication required. Please scan your face at /teacher/auth before performing this action.'}), 403
    """
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401

    # Staff Face Auth is required for finalization
    if not session.get('teacher_face_verified'):
        return jsonify({'success': False, 'message': 'Staff Face Authentication required. Please perform face authentication before approving attendance.'}), 403
        
    data = request.get_json() or {}
    date_str = data.get('date', date.today().strftime('%Y-%m-%d'))
    dept = data.get('department', '').strip()
    year = data.get('year', '').strip()
    section = data.get('section', '').strip()
    
    teacher_record = User.query.filter_by(id=session.get('user_id')).first()
    if not dept or not section:
        if teacher_record:
            dept = dept or teacher_record.department_or_class
            year = year or teacher_record.year
            section = section or teacher_record.section
            
    db.session.commit()
    
    # Generate Parent Alert Logs
    parent_logs = []
    from flask import current_app
    # Verify authorization
    from models import ClassSection
    mapping = ClassSection.query.filter_by(
        institution_id=teacher_record.institution_id,
        department=dept,
        year=year,
        section=section
    ).first()
    
    if mapping:
        if not verify_class_access(teacher_record, mapping):
            return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to submit review for this class section.'}), 403
    else:
        is_own_class = (
            dept.lower() == (teacher_record.department_or_class or '').lower() and
            section.lower() == (teacher_record.section or '').lower()
        )
        if not is_own_class:
            return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to submit review for this class section.'}), 403
        
    # Get or lazy create session
    sess_rec = get_or_create_daily_session(dept, year, section, target_date)
    # Ensure session class_section_id is set
    if mapping and not sess_rec.class_section_id:
        sess_rec.class_section_id = mapping.id
        db.session.commit()
    
    success, res = submit_attendance_session_to_advisor(sess_rec.id, user_id=session.get('user_id'), is_automatic=False)
    if success:
        return jsonify({
            'success': True,
            'message': res['message'],
            'stats': res['stats']
        })
    
    if not sess_rec:
        return jsonify({'success': False, 'message': 'No attendance session found for today.'}), 404
        
def api_approve_finalize():
    """
    Class Advisor approves and finalizes daily attendance.
    Attendance session becomes permanently locked.
    Sends WhatsApp messages to parents of remaining absent students.
    """
            'present': len(students) - len(absent_students),
            'absent': len(absent_students)
        }
    })

@app.route('/api/attendance/today_present', methods=['GET'])
def api_today_present():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    # Get attendance mapping
    student_ids = [s.id for s in students]
    attendance_records = Attendance.query.filter(
        Attendance.user_id.in_(student_ids),
    
    return jsonify([s.to_dict() for s in results])

@app.route('/api/teacher/logout', methods=['GET', 'POST'])
def api_teacher_logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    """AI Assistant Chatbot endpoint using OpenAI RAG fallback to Local SQL query parsing."""
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'success': False, 'message': 'Message is required.'}), 400
        
    user_message = data['message'].strip()
    if not user_message:
        return jsonify({'success': False, 'message': 'Message cannot be empty.'}), 400
        
    # Gather Database Context
    today = date.today()
            failed_log = NotificationLog(
                class_id=f"{dept} {year} - {section}",
                class_section_id=mapping.id,
                student_id=s.id,
                recipient_name=f"Parent of {s.name}",
                recipient_contact="N/A",
                type="Parent Absent Alert",
                channel="WhatsApp",
                message=f"Parent alert for {s.name} ({s.reg_number or 'N/A'})",
                if st:
            if not mapping.advisor.face_registered:
                return jsonify({'success': False, 'message': 'Attendance finalization blocked: The mapped Class Advisor has not registered their face. Please complete Advisor Face Registration first.'}), 403
        else:
            return jsonify({'success': False, 'message': 'Attendance finalization blocked: No Class Advisor is mapped for this class section.'}), 403
    else:
        is_own_class = (
            dept.lower() == (teacher_record.department_or_class or '').lower() and
            section.lower() == (teacher_record.section or '').lower()
        )
        if not is_own_class:
            return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to finalize attendance for this class section.'}), 403
        
    from models import AttendanceSession
    sess_rec = AttendanceSession.query.filter_by(
        department=dept,
        year=year,
        section=section,
        attendance_date=target_date
    ).first()
    
    if not sess_rec:
        return jsonify({'success': False, 'message': 'No attendance session found for today.'}), 404
        
    if sess_rec.status == 'Finalized':
    return jsonify({'success': True, 'reply': reply, 'source': 'Local Parser'})



# ----------------- SETTINGS & FACE REGISTRATION APIs -----------------
        'overall_rate': round(overall_rate, 1),
        'present_students': present_students
    })

@app.route('/api/student/search', methods=['GET'])
        'message': f"Attendance approved & finalized. Parent WhatsApp alerts triggered for {len(parent_logs)} absentees.",
        'stats': {
            'total': len(students),
            'present': len(students) - len(absent_students),
            'absent': len(absent_students)
        }
    })

@app.route('/api/attendance/today_present', methods=['GET'])
def api_today_present():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'User not found'}), 404
        
    # Resolve Active ClassSection
    cs = resolve_active_class_section(user)
    
        return jsonify({'success': False, 'message': 'User not found'}), 404
        
    # Resolve Active ClassSection
    cs = resolve_active_class_section(user)
    
    if cs:
        matched_students = get_students_for_class(user, cs)
        total_students = len(matched_students)
        student_ids = [s.id for s in matched_students]
    else:
        matched_students = []
        total_students = 0
        student_ids = []
        
    today = date.today()
    present_students = []
    if student_ids:
        today_present = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Present').count()
        today_absent = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Absent').count()
        recorded_ids = [a.user_id for a in Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today).all()]
        year = data.get('year', '').strip()
        section = data.get('section', 'A').strip()
        mobile_number = data.get('mobile_number', '').strip()
        parent_phone = data.get('parent_phone', '').strip()
        password_plain = data.get('password', 'password123').strip() or 'password123'
        
        if not reg_num or not dept or not year or not parent_phone:
            return jsonify({'success': False, 'message': 'Missing required student fields (Register Number, Dept, Year, and Parent Mobile Number are required).'}), 400
            
        import re
                f"I found a record matching **{matched_student.name}**:\n"
                f"- Register Number (Email): {matched_student.email}\n"
                f"- Department: {matched_student.department_or_class} ({matched_student.year or 'N/A'})\n"
                f"- Overall Attendance Rate: {pct:.1f}% ({pres}/{tot} days)"
            )
        else:
            reply = (
                "Hi! I am the SmartFace Local AI Helper. I couldn't reach the online LLM, but I can still answer basic questions about attendance stats and records!\n\n"
                "Try asking me:\n"
                "- *'Who is absent today?'*\n"
        
    cs = resolve_active_class_section(user)
    if user.role == 'admin':
        base_query = User.query.filter_by(role='student', institution_id=user.institution_id)
    else:
        if not cs:
            return jsonify([])
        base_query = User.query.filter_by(role='student', class_section_id=cs.id)
        
    results = base_query.filter(
    system_prompt = f"""You are the official AI Assistant for the NexAttend AI - Smart Face Recognition Attendance and Student Management System.

Your responsibility is to help users understand and use the application.
You know every page, feature, module, button, and workflow of the application.
When a user asks where a feature is located, explain the exact navigation path step-by-step.

APPLICATION STRUCTURE:
1. Login Page
   - User Login
   - Admin Login
   - Forgot Password
2. Dashboard
   - Total Students
# REPAIRED:    - Today's Attendance
   - Attendance Percentage
   - Quick Access Cards
3. Student Management
   - Add Student
   - Edit Student
   - Delete Student
            return jsonify({'success': False, 'message': 'Missing required teacher fields.'}), 400
            
        # Check duplicate teacher ID
        existing = User.query.filter_by(email=teacher_id).first()
        if existing:
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
            
        new_teacher = User(
            name=name,
            email=teacher_id,
        base_query = User.query.filter_by(role='student', institution_id=user.institution_id)
    else:
        if not cs:
            return jsonify([])
        base_query = User.query.filter_by(role='student', class_section_id=cs.id)
        
    results = base_query.filter(
        (User.email.like(f"%{query}%")) | 
        (User.name.like(f"%{query}%"))
    ).limit(10).all()
    
    return jsonify([s.to_dict() for s in results])

@app.route('/api/teacher/logout', methods=['GET', 'POST'])
def api_teacher_logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    """AI Assistant Chatbot endpoint using OpenAI RAG fallback to Local SQL query parsing."""
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'success': False, 'message': 'Message is required.'}), 400
        
    user_message = data['message'].strip()
    if not user_message:
        return jsonify({'success': False, 'message': 'Message cannot be empty.'}), 400
        
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        
    cs = resolve_active_class_section(user)
    if not cs:
        return jsonify({'success': False, 'message': 'No active class section resolved.'}), 400
        
    students = get_students_for_class(user, cs)
    total_students = len(students)
    student_ids = [s.id for s in students]
    
    # Gather Database Context for authorized class section
    today = date.today()
    today_present = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Present').count() if student_ids else 0
    today_absent = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Absent').count() if student_ids else 0
    
    recorded_ids = [a.user_id for a in Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today).all()] if student_ids else []
    unmarked_count = len(set(student_ids) - set(recorded_ids))
    effective_absent = today_absent + unmarked_count
    
    attendance_rate = 0.0
    if total_students > 0:
        attendance_rate = round((today_present / total_students) * 100, 1)
        
    students_list = []
    for s in students:
        students_list.append(f"- Name: {s.name}, Reg: {s.email}, Dept: {s.department_or_class}, Year: {s.year or 'N/A'}, Parent Phone: {s.parent_phone or 'N/A'}")
    students_context = "\n".join(students_list) if students_list else "No students registered in your class yet."
    
    today_records = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today).all() if student_ids else []
    attendance_list = []
    for a in today_records:
        student_name = a.user.name if a.user else "Unknown"
        attendance_list.append(f"- Student: {student_name}, Status: {a.status}, Time: {a.time.strftime('%H:%M') if a.time else 'N/A'}, Marked By: {a.marked_by}")
    attendance_context = "\n".join(attendance_list) if attendance_list else "No attendance logged today yet."
    
    student_reg_numbers = [s.reg_number for s in students if s.reg_number]
    audit_logs = AuditLog.query.filter(AuditLog.register_number.in_(student_reg_numbers)).order_by(AuditLog.timestamp.desc()).limit(5).all() if student_reg_numbers else []
        new_teacher = User(
            name=name,
            email=teacher_id,
            password=generate_password_hash("password123"),
            institution_type="College",
            institution_name="VSB Engineering College",
        (User.email.like(f"%{query}%")) | 
        (User.name.like(f"%{query}%"))
    ).limit(10).all()
    
    return jsonify([s.to_dict() for s in results])

@app.route('/api/teacher/logout', methods=['GET', 'POST'])
def api_teacher_logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    """AI Assistant Chatbot endpoint using OpenAI RAG fallback to Local SQL query parsing."""
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({'success': False, 'message': 'Message is required.'}), 400
        
    user_message = data['message'].strip()
    if not user_message:
    success = save_ai_config(provider, api_key)
    if success:
        return jsonify({'success': True, 'message': f'AI Assistant config updated to {provider.upper()}!'})
    else:
        return jsonify({'success': False, 'message': 'Failed to save configuration.'}), 500
4. Face Recognition Attendance
   - Open Camera
   - Scan Face
   - Verify Student
   - Mark Attendance Automatically
5. Attendance Records
   - Daily Attendance
   - Monthly Attendance
   - Attendance History
   - Attendance Reports
6. Attendance Correction
   - Teacher Authentication
   - Change Absent to Present
   - Enter Reason
   - Save Changes
   - Audit Log Entry
7. Analytics Dashboard
   - Attendance Charts
   - Student Performance Statistics
   - Attendance Trends
8. Notifications
   - Parent WhatsApp Alerts
   - Absence Notifications
   - Attendance Updates
9. Settings
   - Profile Management
   - Password Change
   - System Configuration

REAL-TIME DATABASE CONTEXT:
- SYSTEM DATE: {today.strftime('%A, %B %d, %Y')}
- STATISTICS TODAY:
  * Total Enrolled Students: {total_students}
  * Marked Present Today: {today_present}
  * Absent / Unmarked Today: {effective_absent}
  * Attendance Rate Today: {attendance_rate}%

- REGISTERED STUDENTS:
{students_context}
@app.route('/api/settings/delete_user', methods=['POST'])
def api_delete_user():
    """Completely deletes a student or teacher record from the database, including attendance and face files."""
    data = request.get_json()
    if not data or 'role' not in data or 'user_id' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    role = data['role']
    user_id = int(data['user_id'])
    
    user = User.query.get(user_id)
    if role == 'student':
        parent_dir = app.config['STUDENT_FACES_DIR']
    elif role == 'teacher':
        parent_dir = app.config['TEACHER_FACES_DIR']
    else:
        return jsonify({'success': False, 'message': 'Invalid role.'}), 400
        
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
    # Delete face snapshots from disk
        email = data.get('email', '').strip() # subject spec
        mobile_number = data.get('mobile_number', '').strip()
        dept = data.get('department', 'Computer Science').strip()
        section = data.get('section', 'A').strip()
        password_plain = data.get('password', 'password123').strip() or 'password123'
        
        if not teacher_id or not email:
            return jsonify({'success': False, 'message': 'Missing required teacher fields.'}), 400
            
        # Check duplicate teacher ID
        existing = User.query.filter_by(email=teacher_id).first()
        if existing:
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
            
        new_teacher = User(
            name=name,
            email=teacher_id,
            password=generate_password_hash(password_plain),
            institution_type="College",
            institution_name="VSB Engineering College",
            department_or_class=dept,
            year="3rd Year",
            section=section,
            role=role,
            mobile_number=mobile_number or None
        )
        db.session.add(new_teacher)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Teacher {name} registered successfully.'})
        
    return jsonify({'success': True, 'message': f"User record for {user.name} and associated data deleted successfully."})

@app.route('/api/settings/save_advisor_map', methods=['POST'])
def api_save_advisor_map():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    data = request.get_json() or {}
    mapping_id = data.get('id')
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
            
        new_teacher = User(
            name=name,
            email=teacher_id,
            password=generate_password_hash(password_plain),
            institution_type=inst_type,
            institution_name=inst_name,
            department_or_class=dept,
            year="3rd Year" if inst_type == "College" else None,
            section=section,
            role=role,
            mobile_number=mobile_number or None
        )
        db.session.add(new_teacher)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Teacher {name} registered successfully.'})
        
    return jsonify({'success': False, 'message': 'Invalid role.'}), 400

@app.route('/api/settings/seed', methods=['POST'])
def api_seed_data():
    """Seeds the DB with initial students and teachers for immediate usability."""
    # Check if data already exists
    if User.query.count() > 0:
        return jsonify({'success': False, 'message': 'Database already seeded.'})
    if not mapping:
        mapping = ClassSection(
            department=dept,
            year=year,
            section=section,
            advisor_name=advisor_name,
            advisor_email=advisor_email,
            advisor_phone=advisor_phone
        )
        db.session.add(mapping)
    else:
        mapping.department = dept
        mapping.year = year
        mapping.section = section
        mapping.advisor_name = advisor_name
        mapping.advisor_email = advisor_email
        mapping.advisor_phone = advisor_phone
        
    try:
        db.session.commit()
        return jsonify({'success': True, 'message': f'Class advisor mapping saved successfully for {dept} {year} - {section}.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

    elif 'register' in query_lower or 'enroll' in query_lower or 'add student' in query_lower:
        reply = (
            "### How to Register a Student / Face:\n"
            "1. Go to the **Settings** page.\n"
            "2. Under **Add Student / Teacher Record**, select the role (`Student`), fill in the details (Name, Reg Number, Department, Parent WhatsApp mobile number, parent email) and click **Save User Record**.\n"
            "3. The student will immediately appear in the **Face Registration Kiosk** select box on that same page.\n"
            "4. Select the student, click **Start Registration Camera**, and take **5 snapshots** in slightly different angles (Smile, Neutral, Left, Right, etc.).\n"
            "5. The system automatically trains the face recognition model upon the 5th snapshot!"
        )
        
    # Check correction rules query
    elif 'correct' in query_lower or 'modify' in query_lower or 'change' in query_lower or 'rule' in query_lower:
        reply = (
            "### Attendance Correction Rules:\n"
@app.route('/api/settings/add_user', methods=['POST'])
def api_add_user():
    """Adds a new student or teacher record to the database."""
    creator_id = session.get('user_id')
    creator = User.query.get(creator_id) if creator_id else None
    if not creator:
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    data = request.get_json()
    if not data or 'role' not in data or 'name' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    role = data['role']
    name = data['name'].strip()
    
    if not name:
        return jsonify({'success': False, 'message': 'Name cannot be empty.'}), 400
        
    inst_type = creator.institution_type
    inst_name = creator.institution_name
    
    if role == 'student':
        reg_num = data.get('register_number', '').strip()
        email = data.get('email', '').strip() or reg_num
        mobile_number = data.get('mobile_number', '').strip()
            
    return jsonify({'success': True, 'reply': reply, 'source': 'Local Parser'})



# ----------------- SETTINGS & FACE REGISTRATION APIs -----------------

@app.route('/api/settings/save_ai_config', methods=['POST'])
def api_save_ai_config():
    """Saves AI provider selection and API keys to local config."""
        return jsonify({'success': False, 'message': 'Invalid AI provider.'}), 400
        
    # If the key contains asterisks, they did not edit the masked value, so preserve the existing key
    if '*' in api_key or api_key == '******':
        current_config = load_ai_config()
        api_key = current_config.get('api_key', '')
        
    if not api_key:
@app.route('/api/settings/set_admin_class', methods=['POST'])
def api_set_admin_class():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    data = request.get_json() or {}
    class_id = data.get('class_id')
    if class_id:
        session['admin_selected_class_id'] = int(class_id)
        return jsonify({'success': False, 'message': 'API Key cannot be empty.'}), 400
        
    success = save_ai_config(provider, api_key)
    if success:
        return jsonify({'success': True, 'message': f'AI Assistant config updated to {provider.upper()}!'})
    else:


@app.route('/api/settings/add_user', methods=['POST'])
def api_add_user():
    """Adds a new student or teacher record to the database."""
    creator_id = session.get('user_id')
    creator = User.query.get(creator_id) if creator_id else None
    if not creator:
        
    dept = sess_rec.department
    year = sess_rec.year
    section = sess_rec.section
    target_date = sess_rec.attendance_date
    
    # Get Class Advisor mapping
    mapping = ClassSection.query.filter_by(department=dept, year=year, section=section).first()
    if not mapping or not mapping.advisor_name or not mapping.advisor_phone:
        return False, f"Warning: No Class Advisor is currently assigned to this class ({dept} {year} - {section}). Review submission aborted."
        return jsonify({'success': False, 'message': 'Name cannot be empty.'}), 400
        
    inst_type = creator.institution_type
    inst_name = creator.institution_name
    
    if role == 'student':
        reg_num = data.get('register_number', '').strip()
        email = data.get('email', '').strip() or reg_num
        mobile_number = data.get('mobile_number', '').strip()
        parent_phone = data.get('parent_phone', '').strip()
        password_plain = data.get('password', 'password123').strip() or 'password123'
        
        # Determine department, year, and section based on ClassSection mapping
        from models import ClassSection
        cs = None
        
        if creator.role == 'admin':
            class_id = data.get('class_id')
            if class_id:
                cs = ClassSection.query.get(class_id)
                if cs and cs.institution_id != creator.institution_id:
                    return jsonify({'success': False, 'message': 'Access Denied: Class section belongs to another institution.'}), 403
        else:
            # Teacher or Advisor
            cs = resolve_active_class_section(creator)
            
        if not cs:
            # Fallback to form parameters if no ClassSection resolved
            dept = data.get('department', '').strip()
            year = data.get('year', '').strip()
            institution_type=inst_type,
            institution_name=inst_name,
            department_or_class=dept,
            year="3rd Year" if inst_type == "College" else None,
            section=section,
            role=role,
            mobile_number=mobile_number or None
        )
        db.session.add(new_teacher)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Teacher {name} registered successfully.'})
        
    return jsonify({'success': False, 'message': 'Invalid role.'}), 400

@app.route('/api/settings/set_admin_class', methods=['POST'])
def api_set_admin_class():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    data = request.get_json() or {}
    class_id = data.get('class_id')
    for d in active_dates:
        tot = Attendance.query.filter_by(date=d).count()
        pres = Attendance.query.filter_by(date=d, status='Present').count()
        pct = (pres / tot * 100) if tot > 0 else 100.0
        trend_present_pct.append(round(pct, 1))
        
    return jsonify({
        'summary': {
            'present': present_count,
            'absent': absent_count,
            'total': total_attendance
        },
        'departments': {
            'labels': dept_labels,
            'data': dept_present_rates
        },
        'trend': {
            'labels': trend_labels,
            'data': trend_present_pct
        }
    })


# ----------------- DB SETUP & RUN -----------------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Run the server on all interfaces, port 5000
    app.run(host='0.0.0.0', port=5000, debug=True)
        
    elif role == 'teacher':
        # Only admins can create teachers
        if creator.role != 'admin':
            return jsonify({'success': False, 'message': 'Access Denied: Only Admins can register teachers.'}), 403
            
        teacher_id = data.get('teacher_id', '').strip() # email
        email = data.get('email', '').strip() # subject spec
        mobile_number = data.get('mobile_number', '').strip()
        dept = data.get('department', 'Computer Science').strip()
        section = data.get('section', 'A').strip()
        password_plain = data.get('password', 'password123').strip() or 'password123'
        
        if not teacher_id or not email:
            return jsonify({'success': False, 'message': 'Missing required teacher fields.'}), 400
            
        # Check duplicate teacher ID
        existing = User.query.filter_by(email=teacher_id).first()
        if existing:
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
@app.route('/api/settings/enroll', methods=['POST'])
def api_enroll_face():
    """
    Saves snapshot (1 to 5) for a user face.
    If it's the 5th snapshot, trains the model and marks user as registered.
    """
    data = request.get_json()
    if not data or 'image' not in data or 'user_type' not in data or 'user_id' not in data or 'snapshot_index' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
            return jsonify({'success': False, 'message': 'Access Denied: Only Admins can register teachers.'}), 403
            
        teacher_id = data.get('teacher_id', '').strip() # email
        email = data.get('email', '').strip() # subject spec
        mobile_number = data.get('mobile_number', '').strip()
        dept = data.get('department', 'Computer Science').strip()
        section = data.get('section', 'A').strip()
        password_plain = data.get('password', 'password123').strip() or 'password123'
        
        if not teacher_id or not email:
        mobile_number = data.get('mobile_number', '').strip()
        dept = data.get('department', 'Computer Science').strip()
        section = data.get('section', 'A').strip()
        password_plain = data.get('password', 'password123').strip() or 'password123'
        
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
            
        new_teacher = User(
            name=name,
            email=teacher_id,
            password=generate_password_hash(password_plain),
            institution_type=inst_type,
            institution_name=inst_name,
            institution_id=creator.institution_id,
            department_or_class=dept,
            year="3rd Year" if inst_type == "College" else None,
            section=section,
            role=role,
            mobile_number=mobile_number or None
        )
        db.session.add(new_teacher)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Teacher {name} registered successfully.'})
        
    return jsonify({'success': False, 'message': 'Invalid role.'}), 400

@app.route('/api/settings/set_admin_class', methods=['POST'])
def api_set_admin_class():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        class_label = dept
        if mobile_number and is_advisor:
            try:
                class_info = f"{dept} - Section {section}"
                welcome_msg = (
                    f"Welcome to NexAttend AI! 🎉\n\n"
                    f"You have been registered as a Class Advisor at {inst_name}.\n\n"
                    f"📚 Assigned Class: {class_info}\n"
                    f"📧 Login Email: {teacher_id}\n"
                    f"🔑 Temporary Password: {password_plain}\n\n"
                    f"Please log in at your institution's NexAttend AI portal and complete your Face Registration to activate your account.\n\n"
                    f"Thank you!"
                )
                NotificationService.send_whatsapp(
                    to_number=mobile_number,
                    message=welcome_msg,
                    recipient_name=name,
                    msg_type="Advisor Welcome Notification"
                )
            except Exception as e:
                print(f"[WhatsApp] Failed to send advisor welcome: {e}")
        
        return jsonify({
            'success': True,
    FaceService.train_model(user_type)
    
    return jsonify({'success': True, 'message': f"Facial record deleted for {user.name}."})

@app.route('/api/settings/delete_user', methods=['POST'])
        return jsonify({'success': False, 'message': f"Error seeding database: {e}"}), 500

@app.route('/api/settings/enroll', methods=['POST'])
def api_enroll_face():
    """
    Saves snapshot (1 to 5) for a user face.
    If it's the 5th snapshot, trains the model and marks user as registered.
    """
    creator_id = session.get('user_id')
    creator = User.query.get(creator_id) if creator_id else None
    if not creator:
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    data = request.get_json()
    if not data or 'image' not in data or 'user_type' not in data or 'user_id' not in data or 'snapshot_index' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    user_type = data['user_type'] # 'student' or 'teacher'
    user_id = int(data['user_id'])
    snapshot_index = int(data['snapshot_index']) # 1 to 5
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
    # IDOR Scope Check
    if user.role == 'student':
        if user.institution_id != creator.institution_id:
            return jsonify({'success': False, 'message': 'Access Denied: Institution mismatch.'}), 403
        if creator.role == 'teacher':
            cs = resolve_active_class_section(creator)
            if not cs or user.class_section_id != cs.id:
                return jsonify({'success': False, 'message': 'Access Denied: Class section mismatch.'}), 403
    elif user.role == 'teacher':
        if creator.role != 'admin' or user.institution_id != creator.institution_id:
            return jsonify({'success': False, 'message': 'Access Denied: Only Admins can register teacher faces.'}), 403
            
    img_bgr = decode_base64_image(data['image'])
    if img_bgr is None:
        return jsonify({'success': False, 'message': 'Failed to decode image.'}), 400
def submit_attendance_session_to_advisor(session_id, user_id=None, is_automatic=False):
    """
    Core Step-1 attendance submission logic shared by manual and automatic flows.
    """
    from models import AttendanceSession, ClassSection, User, Attendance, NotificationLog
    
    # Use database locking or immediate state checks for idempotency
    sess_rec = AttendanceSession.query.filter_by(id=session_id).first()
    if not sess_rec:
        return False, "Attendance session not found."
        # Run model training
        train_success, train_msg = FaceService.train_model(user_type)
        if not train_success:
            return jsonify({
                'success': True, 
                'message': f"Faces saved but model training failed: {train_msg}"
            })
        else:
            return jsonify({
                'success': True,
                'message': "Face enrolled and recognition model trained successfully!"
            })
    return jsonify({'success': True, 'message': f"Snapshot {snapshot_index}/5 saved."})

@app.route('/api/settings/delete_face', methods=['POST'])
@app.route('/api/settings/enroll', methods=['POST'])
def api_enroll_face():
    """
    Saves snapshot (1 to 5) for a user face.
    If it's the 5th snapshot, trains the model and marks user as registered.
    """
    creator_id = session.get('user_id')
    creator = User.query.get(creator_id) if creator_id else None
    if not creator:
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    data = request.get_json()
    if not data or 'image' not in data or 'user_type' not in data or 'user_id' not in data or 'snapshot_index' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    user_type = data['user_type'] # 'student' or 'teacher'
    user_id = int(data['user_id'])
    snapshot_index = int(data['snapshot_index']) # 1 to 5
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
    # IDOR Scope Check
    if user.role == 'student':
        if user.institution_id != creator.institution_id:
            return jsonify({'success': False, 'message': 'Access Denied: Institution mismatch.'}), 403
        if creator.role == 'teacher':
            cs = resolve_active_class_section(creator)
            if not cs or user.class_section_id != cs.id:
                return jsonify({'success': False, 'message': 'Access Denied: Class section mismatch.'}), 403
    elif user.role == 'teacher':
        # Allow self-enrollment (teacher/advisor registers their own face)
        if creator.id == user.id:
            pass  # Self-enrollment: allowed
        elif creator.role == 'admin' and user.institution_id == creator.institution_id:
            pass  # Admin enrolling staff in same institution: allowed
        else:
            return jsonify({'success': False, 'message': 'Access Denied: Only Admins can register staff faces, or staff may register their own face.'}), 403
            
    img_bgr = decode_base64_image(data['image'])
    if img_bgr is None:
        return jsonify({'success': False, 'message': 'Failed to decode image.'}), 400
        
    success, message = FaceService.save_face_snapshots(img_bgr, user_type, user_id, snapshot_index)
        
    # Retrain facial models
    try:
        FaceService.train_model(role)
    except Exception as e:
        print(f"Error retraining model: {e}")
        
    return jsonify({'success': True, 'message': f"User record for {user.name} and associated data deleted successfully."})

@app.route('/api/settings/save_advisor_map', methods=['POST'])
def api_save_advisor_map():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    data = request.get_json() or {}
    mapping_id = data.get('id')
    dept = data.get('department', '').strip()
    year = data.get('year', '').strip()
    section = data.get('section', '').strip()
    advisor_name = data.get('advisor_name', '').strip()
    advisor_email = data.get('advisor_email', '').strip()
    advisor_phone = data.get('advisor_phone', '').strip()
    
    if not dept or not section or not advisor_name or not advisor_email or not advisor_phone:
        return jsonify({'success': False, 'message': 'All fields are required.'}), 400
        
    # E.164 phone validation (starts with + followed by 10-15 digits)
    import re
    if not re.match(r'^\+[1-9]\d{1,14}$', advisor_phone):
        return jsonify({'success': False, 'message': 'WhatsApp Number must be in E.164 format (e.g. +919876543210).'}), 400
        
    from models import ClassSection
    
    mapping = None
    if mapping_id:
        mapping = ClassSection.query.get(mapping_id)
        
    # Check for duplicate
    duplicate = ClassSection.query.filter_by(department=dept, year=year, section=section).first()
    if duplicate and (not mapping or duplicate.id != mapping.id):
        return jsonify({'success': False, 'message': f'A class advisor mapping already exists for {dept} {year} - {section}.'}), 400
        
    if not mapping:
        mapping = ClassSection(
            department=dept,
            year=year,
            section=section,
            advisor_name=advisor_name,
            advisor_email=advisor_email,
            advisor_phone=advisor_phone
        )
        db.session.add(mapping)
    else:
        mapping.department = dept
        mapping.year = year
        mapping.section = section
        mapping.advisor_name = advisor_name
        mapping.advisor_email = advisor_email
        mapping.advisor_phone = advisor_phone
        
    try:
        db.session.commit()
        return jsonify({'success': True, 'message': f'Class advisor mapping saved successfully for {dept} {year} - {section}.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@app.route('/api/settings/delete_advisor_map', methods=['POST'])
def api_delete_advisor_map():
    if not session.get('logged_in'):
        print(f"Error retraining model: {e}")
        
    return jsonify({'success': True, 'message': f"User record for {user.name} and associated data deleted successfully."})

@app.route('/api/settings/save_advisor_map', methods=['POST'])
def api_save_advisor_map():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'success': False, 'message': 'Access Denied: Only Admins can manage advisor mappings.'}), 403
        
    data = request.get_json() or {}
    mapping_id = data.get('id')
    dept = data.get('department', '').strip()
    year = data.get('year', '').strip()
    section = data.get('section', '').strip()
    advisor_name = data.get('advisor_name', '').strip()
    advisor_email = data.get('advisor_email', '').strip()
    elif user.role == 'teacher':
        if creator.role != 'admin' or user.institution_id != creator.institution_id:
            return jsonify({'success': False, 'message': 'Access Denied: Only Admins can delete teachers.'}), 403
            
    if role == 'student':
        parent_dir = app.config['STUDENT_FACES_DIR']
    elif role == 'teacher':
        parent_dir = app.config['TEACHER_FACES_DIR']
    else:
        return jsonify({'success': False, 'message': 'Invalid role.'}), 400
        
    # Delete face snapshots from disk
    user_dir = os.path.join(parent_dir, str(user_id))
    if os.path.exists(user_dir):
        try:
            for file in os.listdir(user_dir):
                os.remove(os.path.join(user_dir, file))
            os.rmdir(user_dir)
        except Exception as e:
            print(f"Error removing user face files: {e}")

def run_attendance_scheduler(app_instance):
    """
    Background scheduler loop that runs every 30 seconds to auto-submit Draft sessions at End Time.
    """
    import time
    from datetime import datetime, date
    from models import db, ClassSection, AttendanceSession
    
    print("Attendance End-Time Scheduler initialized.", flush=True)
    while True:
        try:
            time.sleep(30)
            
            with app_instance.app_context():
                now = datetime.now()
                current_date = now.date()
                current_time = now.time()
                
                # Fetch all class sections
                classes = ClassSection.query.all()
                for c_sec in classes:
                    # Auto-submit must be enabled, and end_time must be reached
                    if c_sec.auto_submit_enabled and c_sec.end_time and c_sec.end_time <= current_time:
                        # Find Draft session for today
                        sess_rec = AttendanceSession.query.filter_by(
                            department=c_sec.department,
                            year=c_sec.year,
                            section=c_sec.section,
                            attendance_date=current_date,
        mapping.advisor_phone = advisor_phone
        mapping.advisor_id = advisor_id
        
    try:
        db.session.commit()
    dept = data.get('department', '').strip()
    year = data.get('year', '').strip()
    section = data.get('section', '').strip()
    advisor_name = data.get('advisor_name', '').strip()
    advisor_email = data.get('advisor_email', '').strip()
                            if success:
                                print(f"[SCHEDULER] Auto-submitted session successfully: {res['message']}", flush=True)
                            else:
                                print(f"[SCHEDULER] Auto-submission failed: {res}", flush=True)
                                
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}", flush=True)


# ----------------- DB SETUP & RUN -----------------

if __name__ == '__main__':
@app.route('/api/analytics/data')
def api_analytics_data():
    """Aggregates analytics stats for Chart.js"""
    # 1. Overall attendance rates (Present vs Absent)
    total_attendance = Attendance.query.count()
    present_count = Attendance.query.filter_by(status='Present').count()
    absent_count = Attendance.query.filter_by(status='Absent').count()
    
        department=dept,
        year=year,
        section=section
    ).first()
    if duplicate and (not mapping or duplicate.id != mapping.id):
        return jsonify({'success': False, 'message': f'A class advisor mapping already exists for {dept} {year} - {section}.'}), 400
        
    advisor_user = User.query.filter_by(email=advisor_email).first()
    advisor_id = advisor_user.id if advisor_user else None
        
    if not mapping:
        mapping = ClassSection(
            department=dept,
            year=year,
            section=section,
            advisor_name=advisor_name,
                print(f"Error migrating class_sections end_time: {e}")
        # 3. auto_submit_enabled on class_sections
        try:
            db.session.execute(text("SELECT auto_submit_enabled FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN auto_submit_enabled BOOLEAN NOT NULL DEFAULT 1"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections auto_submit_enabled: {e}")
        # 4. is_automatic on attendance_sessions
        try:
            db.session.execute(text("SELECT is_automatic FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN is_automatic BOOLEAN NOT NULL DEFAULT 0"))
                db.session.commit()
def submit_attendance_session_to_advisor(session_id, user_id=None, is_automatic=False):
    """
    Core Step-1 attendance submission logic shared by manual and automatic flows.
    """
                                is_automatic=True
                            )
                            if success:
                                print(f"[SCHEDULER] Auto-submitted session successfully: {res['message']}", flush=True)
                            else:
                                print(f"[SCHEDULER] Auto-submission failed: {res}", flush=True)
                                
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}", flush=True)


# ----------------- DB SETUP & RUN -----------------

if __name__ == '__main__':
    with app.app_context():
        # Dynamic alter migrations to add new columns if they do not exist
        from sqlalchemy import text
        # 1. start_time on class_sections
        try:
            db.session.execute(text("SELECT start_time FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN start_time TIME NOT NULL DEFAULT '09:00:00'"))
                db.session.commit()
        return jsonify({'success': False, 'message': 'Mapping ID required.'}), 400
        
    from models import ClassSection
    mapping = ClassSection.query.get(mapping_id)
    if not mapping:
        return jsonify({'success': False, 'message': 'Mapping record not found.'}), 404
        
    if mapping.institution_id != user.institution_id:
        return jsonify({'success': False, 'message': 'Access Denied.'}), 403
        
    try:
            except Exception as e:
                db.session.rollback()
        return jsonify({'success': True, 'message': 'Advisor mapping deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500


# ----------------- ANALYTICS API -----------------
            record = Attendance(
                user_id=s.id,
                date=target_date,
                time=now_time,
                status='Absent',
                db.session.rollback()
                print(f"Error migrating class_sections auto_submit_enabled: {e}")
        # 4. is_automatic on attendance_sessions
        try:
            db.session.execute(text("SELECT is_automatic FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN is_automatic BOOLEAN NOT NULL DEFAULT 0"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating attendance_sessions is_automatic: {e}")

        # 5. institution_id on class_sections
        try:
            db.session.execute(text("SELECT institution_id FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN institution_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections institution_id: {e}")
        # 6. institution_id on users
        try:
            db.session.execute(text("SELECT institution_id FROM users LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE users ADD COLUMN institution_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating users institution_id: {e}")
    else:
        advisor_msg = (
            f"📊 Attendance Review\n\n"
            f"Class: {dept} {year} - {section}\n\n"

# ----------------- SETUP WIZARD -----------------

@app.route('/setup')
def setup_wizard():
    """Full-screen first-time setup wizard. Accessible to logged-in admins."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    user = User.query.get(user_id)
    if not user or user.role != 'admin':
        return redirect(url_for('home'))
    return render_template('setup_wizard.html')


@app.route('/api/setup/status')
def api_setup_status():
    """Returns dynamic setup completion status based on actual DB records."""
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    if not user or user.role != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    from models import ClassSection
    inst_id = user.institution_id
    inst_type = user.institution_type or 'College'

    # Step 1: Institution Setup (admin exists with institution)
    step_institution = bool(inst_id and user.institution_name)

    # Step 2: Admin Created (always true if we reach here)
    step_admin = True

    # Step 3: Class Section Created
    class_sections = ClassSection.query.filter_by(institution_id=inst_id).all() if inst_id else []
    step_class_section = len(class_sections) > 0

    # Step 4: Teacher Added
    teachers = User.query.filter_by(role='teacher', institution_id=inst_id).all() if inst_id else []
    step_teacher = len(teachers) > 0

        c_sec = ClassSection.query.get(class_id)
        if not c_sec:
            return jsonify({'success': False, 'message': 'Class Section not found.'}), 404
            
        try:
            c_sec.start_time = datetime.strptime(start_time_str, '%H:%M').time()
            c_sec.end_time = datetime.strptime(end_time_str, '%H:%M').time()
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid time format. Use HH:MM.'}), 400
            
        c_sec.auto_submit_enabled = auto_submit_enabled
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Time settings updated for {c_sec.department} {c_sec.year} - {c_sec.section}!"
        })


def run_attendance_scheduler(app_instance):
    """
    Background scheduler loop that runs every 30 seconds to auto-submit Draft sessions at End Time.
    """
    import time
    from datetime import datetime, date
    from models import db, ClassSection, AttendanceSession
    
    print("Attendance End-Time Scheduler initialized.", flush=True)
    while True:

# ----------------- SETUP WIZARD -----------------

@app.route('/api/setup/institution', methods=['POST'])
def api_setup_institution():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    if not user or user.role != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    data = request.get_json() or {}
    name = data.get('institution_name', '').strip()
    itype = data.get('institution_type', '').strip()
    
    if not name or itype not in ['School', 'College']:
        return jsonify({'success': False, 'message': 'Invalid Name or Type'}), 400
        
    from models import Institution
        'success': True,
        'message': 'Institution details configured successfully.',
        'institution_name': name,
        'institution_type': itype
# REPAIRED:     })

@app.route('/setup')
def setup_wizard():
    """Full-screen first-time setup wizard. Accessible to logged-in admins."""
    user_id = session.get('user_id')
            c_sec.end_time = datetime.strptime(end_time_str, '%H:%M').time()
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid time format. Use HH:MM.'}), 400
            
        c_sec.auto_submit_enabled = auto_submit_enabled
@app.route('/setup')
def setup_wizard():
    """Full-screen first-time setup wizard. Accessible to logged-in users."""
@app.route('/api/setup/status')
def api_setup_status():
    """Returns dynamic setup completion status based on actual DB records."""
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    if not user or user.role != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    from models import ClassSection
    inst_id = user.institution_id
    inst_type = user.institution_type or 'College'

    from models import Institution
    inst = Institution.query.get(inst_id) if inst_id else None
    step_institution = bool(inst and inst.name)

    # Class Section Created
    class_sections = ClassSection.query.filter_by(institution_id=inst_id).all() if inst_id else []
    step_class_section = len(class_sections) > 0

    # Teacher Added
    teachers = User.query.filter_by(role='teacher', institution_id=inst_id).all() if inst_id else []
    advisor_cs_list = [cs for cs in class_sections if cs.advisor_id]
    advisors_with_face = [cs for cs in advisor_cs_list if cs.advisor and cs.advisor.face_registered]
    students = User.query.filter_by(role='student', institution_id=inst_id).all() if inst_id else []
    students_with_face = [s for s in students if s.face_registered]

    default_start = '09:00'
    default_end = '09:30'
    timing_configured = any(
        (cs.start_time.strftime('%H:%M') != default_start or cs.end_time.strftime('%H:%M') != default_end)
        for cs in class_sections if cs.start_time and cs.end_time
    ) if class_sections else False

    checklist = [
        {'key': 'class_section',   'label': 'Class Section Created',       'done': len(class_sections) > 0},
        {'key': 'teacher',         'label': 'Teacher Account Created',      'done': len(teachers) > 0},
        {'key': 'advisor',         'label': 'Advisor Assigned to Class',    'done': len(advisor_cs_list) > 0},
        {'key': 'advisor_face',    'label': 'Advisor Face Registered',      'done': len(advisors_with_face) == len(advisor_cs_list) and len(advisor_cs_list) > 0},
        {'key': 'advisor_mapping', 'label': 'Advisor Mapping Completed',    'done': len(advisor_cs_list) > 0 and len(advisors_with_face) == len(advisor_cs_list)},
        {'key': 'students',        'label': 'Students Added',               'done': len(students) > 0},
        {'key': 'student_faces',   'label': 'Student Faces Registered',     'done': len(students) > 0 and len(students_with_face) == len(students)},
        {'key': 'timing',          'label': 'Attendance Timing Configured', 'done': timing_configured},
    ]

    ready = all(item['done'] for item in checklist)
    return jsonify({'success': True, 'ready': ready, 'checklist': checklist})


# ----------------- ANALYTICS API -----------------

@app.route('/api/analytics/data')
def api_analytics_data():
        students_with_face = [s for s in students if s.face_registered]
        step_student_faces = len(students_with_face) == len(students)
        student_face_count = len(students_with_face)
    else:
        step_student_faces = False
        student_face_count = 0

    # Attendance Timing Configured (any class section has non-default time range set)
    default_start = '09:00'
            with app_instance.app_context():
                now = datetime.now()
                current_date = now.date()
                current_time = now.time()
                
                # Fetch all class sections
                classes = ClassSection.query.all()
                for c_sec in classes:
                    # Auto-submit must be enabled, and end_time must be reached
                    if c_sec.auto_submit_enabled and c_sec.end_time and c_sec.end_time <= current_time:
                        # Find Draft session for today
                        sess_rec = AttendanceSession.query.filter_by(
                            department=c_sec.department,
                            year=c_sec.year,
                            section=c_sec.section,
                            attendance_date=current_date,
                            status='Draft'
                        ).first()
                        
                        if sess_rec:
                            print(f"[SCHEDULER] Auto-submitting session {sess_rec.id} for {c_sec.department} (end_time {c_sec.end_time} reached).", flush=True)
                            success, res = submit_attendance_session_to_advisor(
                                sess_rec.id, 
                                user_id=None, 
                                is_automatic=True
                            )
                            if success:
                                print(f"[SCHEDULER] Auto-submitted session successfully: {res['message']}", flush=True)
                            else:
                                print(f"[SCHEDULER] Auto-submission failed: {res}", flush=True)
            'teachers': [t.to_dict() for t in teachers],
            'students': [s.to_dict() for s in students]
# REPAIRED:         }
# REPAIRED:     })


@app.route('/api/setup/preflight')
def api_setup_preflight():
    """Returns attendance readiness pre-flight check. Used by Attendance page."""
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    if not user:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    from models import ClassSection
    inst_id = user.institution_id
    inst_type = user.institution_type or 'College'

    class_sections = ClassSection.query.filter_by(institution_id=inst_id).all() if inst_id else []
    teachers = User.query.filter_by(role='teacher', institution_id=inst_id).all() if inst_id else []
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections start_time: {e}")
        # 2. end_time on class_sections
        try:
            db.session.execute(text("SELECT end_time FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
            'labels': trend_labels,
            'data': trend_present_pct
# REPAIRED:         }
# REPAIRED:     })


# ----------------- ATTENDANCE TIME SETTINGS & SCHEDULER -----------------

def submit_attendance_session_to_advisor(session_id, user_id=None, is_automatic=False):
    """
    Core Step-1 attendance submission logic shared by manual and automatic flows.
    """
    from models import AttendanceSession, ClassSection, User, Attendance, NotificationLog
    
    sess_rec = AttendanceSession.query.filter_by(id=session_id).first()
    if not sess_rec:
        return False, "Attendance session not found."
        
    if sess_rec.status != 'Draft':
        return False, f"Cannot submit review. Session is currently in {sess_rec.status} status."
        
    dept = sess_rec.department
    year = sess_rec.year
    section = sess_rec.section
    target_date = sess_rec.attendance_date
    
    teachers_non_advisor = User.query.filter(
        User.role == 'teacher',
        User.institution_id == inst_id,
        User.department_or_class != 'Advisor'
    ).all() if inst_id else []

    # Advisor (role='teacher' and department_or_class='Advisor', or linked as advisor_id)
    advisors_by_dept = User.query.filter(
        User.role == 'teacher',
        User.institution_id == inst_id,
        User.department_or_class == 'Advisor'
    ).all() if inst_id else []
    advisors_mapped_ids = [cs.advisor_id for cs in class_sections if cs.advisor_id]
    advisors_mapped = User.query.filter(User.id.in_(advisors_mapped_ids)).all() if advisors_mapped_ids else []
    advisors = list({a.id: a for a in (advisors_by_dept + advisors_mapped)}.values())

    advisor_cs_list = [cs for cs in class_sections if cs.advisor_id]

    students = User.query.filter_by(role='student', institution_id=inst_id).all() if inst_id else []
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN institution_type VARCHAR(50)"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections institution_type: {e}")
        # 6. institution_id on users
        try:
            db.session.execute(text("SELECT institution_id FROM users LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE users ADD COLUMN institution_id INTEGER"))
                db.session.commit()
        {'key': 'student_faces',   'label': 'Student Faces Registered',     'done': len(students) > 0 and all(s.face_registered for s in students)},
        {'key': 'timing',          'label': 'Attendance Timing Configured', 'done': timing_configured},
# REPAIRED:     ]

    ready = all(item['done'] for item in checklist)
    return jsonify({'success': True, 'ready': ready, 'checklist': checklist})


# ----------------- ANALYTICS API -----------------
            attendance_map[s.id] = record
            
    # Calculate counts
    total_students = len(students)
    present_count = sum(1 for s in students if attendance_map.get(s.id) and attendance_map[s.id].status == 'Present')
    absent_count = sum(1 for s in students if attendance_map.get(s.id) and attendance_map[s.id].status == 'Absent')
    absent_students = [s for s in students if attendance_map.get(s.id) and attendance_map[s.id].status == 'Absent']
    
    # Update session status
    sess_rec.status = 'Submitted'
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating users class_section_id: {e}")
        # 8. class_section_id on attendance_sessions
        try:
            db.session.execute(text("SELECT class_section_id FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            db.session.execute(text("SELECT class_section_id FROM notification_logs LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE notification_logs ADD COLUMN class_section_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating notification_logs class_section_id: {e}")

        # --- DATA MIGRATION / BACKFILL ---
        from models import Institution, User, ClassSection, AttendanceSession, NotificationLog
        
        # 1. Backfill Institutions from legacy user records
        legacy_insts = db.session.query(User.institution_name, User.institution_type).filter(
            User.institution_name != None, User.institution_name != ""
        ).distinct().all()
        
        for name, itype in legacy_insts:
            name_clean = name.strip()
            inst = Institution.query.filter_by(name=name_clean).first()
            if not inst:
                inst = Institution(name=name_clean, institution_type=itype or "College")
            ))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[MIGRATION] Note: uq_institution_class_section index: {e}")

        # --- DATA MIGRATION / BACKFILL ---
        from models import Institution, User, ClassSection, AttendanceSession, NotificationLog
        
        # 1. Backfill Institutions from legacy user records
        legacy_insts = db.session.query(User.institution_name, User.institution_type).filter(
            User.institution_name != None, User.institution_name != ""
        ).distinct().all()
        
        for name, itype in legacy_insts:
            name_clean = name.strip()
            inst = Institution.query.filter_by(name=name_clean).first()
            if not inst:
                inst = Institution(name=name_clean, institution_type=itype or "College")
                db.session.add(inst)
        db.session.commit()
        
        # 2. Link existing Users to their Institution
        for u in User.query.filter(User.institution_id == None).all():
            if u.institution_name:
                inst = Institution.query.filter_by(name=u.institution_name.strip()).first()
                if inst:
                    u.institution_id = inst.id
        db.session.commit()
        
        # 3. Link ClassSections to their Institution
        for cs in ClassSection.query.filter(ClassSection.institution_id == None).all():
            advisor = None
            if cs.advisor_id:
                advisor = User.query.get(cs.advisor_id)
            elif cs.advisor_email:
                advisor = User.query.filter_by(email=cs.advisor_email).first()
                
            if advisor and advisor.institution_id:

# ----------------- ATTENDANCE TIME SETTINGS & SCHEDULER -----------------

def submit_attendance_session_to_advisor(session_id, user_id=None, is_automatic=False):
    """
    Core Step-1 attendance submission logic shared by manual and automatic flows.
    """
    from models import AttendanceSession, ClassSection, User, Attendance, NotificationLog
    
    sess_rec = AttendanceSession.query.filter_by(id=session_id).first()
    if not sess_rec:
        return False, "Attendance session not found."
        
    if sess_rec.status != 'Draft':
        return False, f"Cannot submit review. Session is currently in {sess_rec.status} status."
        
    dept = sess_rec.department
    year = sess_rec.year
    section = sess_rec.section
    target_date = sess_rec.attendance_date
        
        # 5. Link AttendanceSessions to ClassSection
        for sess in AttendanceSession.query.filter(AttendanceSession.class_section_id == None).all():
            c_sections = ClassSection.query.filter_by(
                department=sess.department,
                year=sess.year,
                section=sess.section
            ).all()
            if len(c_sections) == 1:
                sess.class_section_id = c_sections[0].id
            elif len(c_sections) > 1:
                print(f"[MIGRATION WARNING] AttendanceSession ID {sess.id} is ambiguous and could not be resolved.")
        db.session.commit()

        # 6. Link NotificationLogs to ClassSection
        for log in NotificationLog.query.filter(NotificationLog.class_section_id == None).all():
            cs_id = None
            if log.student_id:
                student = User.query.get(log.student_id)
                if student and student.class_section_id:
                    cs_id = student.class_section_id
            if not cs_id and log.advisor_id:
                advisor_class = ClassSection.query.filter_by(advisor_id=log.advisor_id).first()
                if advisor_class:
                    cs_id = advisor_class.id
        import threading
        t = threading.Thread(target=run_attendance_scheduler, args=(app,), daemon=True)
        t.start()
        
    # Run the server on all interfaces, port 5000
    app.run(host='0.0.0.0', port=5000, debug=True)

    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        import threading
        t = threading.Thread(target=run_attendance_scheduler, args=(app,), daemon=True)
        t.start()
        
    # Run the server on all interfaces, port 5000 (HTTP)
    app.run(host='0.0.0.0', port=5000, debug=True)






































































































































































# REPAIRED:                         ).first()
                        
                        if sess_rec:
                            print(f"[SCHEDULER] Auto-submitting session {sess_rec.id} for {c_sec.department} (end_time {c_sec.end_time} reached).", flush=True)
                            success, res = submit_attendance_session_to_advisor(
                                sess_rec.id, 
                                user_id=None, 
                                is_automatic=True
                            )
                            if success:
                                print(f"[SCHEDULER] Auto-submitted session successfully: {res['message']}", flush=True)
                            else:
                                print(f"[SCHEDULER] Auto-submission failed: {res}", flush=True)
                                
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}", flush=True)


# ----------------- DB SETUP & RUN -----------------

if __name__ == '__main__':
    with app.app_context():
        # Ensure institutions and any new tables are created
        db.create_all()
        
        from sqlalchemy import text
        # 1. start_time on class_sections
        try:
            db.session.execute(text("SELECT start_time FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN start_time TIME NOT NULL DEFAULT '09:00:00'"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections start_time: {e}")
        # 2. end_time on class_sections
        try:
            db.session.execute(text("SELECT end_time FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN end_time TIME NOT NULL DEFAULT '09:30:00'"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections end_time: {e}")
        # 3. auto_submit_enabled on class_sections
        try:
            db.session.execute(text("SELECT auto_submit_enabled FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN auto_submit_enabled BOOLEAN NOT NULL DEFAULT 1"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections auto_submit_enabled: {e}")
        # 4. is_automatic on attendance_sessions
        try:
            db.session.execute(text("SELECT is_automatic FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN is_automatic BOOLEAN NOT NULL DEFAULT 0"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating attendance_sessions is_automatic: {e}")

        # 5. institution_id on class_sections
        try:
            db.session.execute(text("SELECT institution_id FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN institution_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections institution_id: {e}")
        # 5b. institution_name on class_sections
        try:
            db.session.execute(text("SELECT institution_name FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN institution_name VARCHAR(150)"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections institution_name: {e}")
        # 5c. institution_type on class_sections
        try:
            db.session.execute(text("SELECT institution_type FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN institution_type VARCHAR(50)"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating class_sections institution_type: {e}")
        # 6. institution_id on users
        try:
            db.session.execute(text("SELECT institution_id FROM users LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE users ADD COLUMN institution_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating users institution_id: {e}")
        # 7. class_section_id on users
        try:
            db.session.execute(text("SELECT class_section_id FROM users LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE users ADD COLUMN class_section_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating users class_section_id: {e}")
        # 8. class_section_id on attendance_sessions
        try:
            db.session.execute(text("SELECT class_section_id FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN class_section_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating attendance_sessions class_section_id: {e}")
        # 9. class_section_id on notification_logs
        try:
            db.session.execute(text("SELECT class_section_id FROM notification_logs LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE notification_logs ADD COLUMN class_section_id INTEGER"))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error migrating notification_logs class_section_id: {e}")

        # 10. Create institution-scoped unique index on class_sections if missing
        try:
            db.session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_institution_class_section "
                "ON class_sections (institution_id, department, year, section)"
            ))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[MIGRATION] Note: uq_institution_class_section index: {e}")

        # --- DATA MIGRATION / BACKFILL ---
        from models import Institution, User, ClassSection, AttendanceSession, NotificationLog
        
        # 1. Backfill Institutions from legacy user records
        legacy_insts = db.session.query(User.institution_name, User.institution_type).filter(
            User.institution_name != None, User.institution_name != ""
        ).distinct().all()
        
        for name, itype in legacy_insts:
            name_clean = name.strip()
            inst = Institution.query.filter_by(name=name_clean).first()
            if not inst:
                inst = Institution(name=name_clean, institution_type=itype or "College")
                db.session.add(inst)
        db.session.commit()
        
        # 2. Link existing Users to their Institution
        for u in User.query.filter(User.institution_id == None).all():
            if u.institution_name:
                inst = Institution.query.filter_by(name=u.institution_name.strip()).first()
                if inst:
                    u.institution_id = inst.id
        db.session.commit()
        
        # 3. Link ClassSections to their Institution
        for cs in ClassSection.query.filter(ClassSection.institution_id == None).all():
            advisor = None
            if cs.advisor_id:
                advisor = User.query.get(cs.advisor_id)
            elif cs.advisor_email:
                advisor = User.query.filter_by(email=cs.advisor_email).first()
                
            if advisor and advisor.institution_id:
                cs.institution_id = advisor.institution_id
                cs.institution_name = advisor.institution_name
                cs.institution_type = advisor.institution_type
            else:
                print(f"[MIGRATION WARNING] ClassSection ID {cs.id} ({cs.department} {cs.year} - {cs.section}) has unresolved institution ownership.")
        db.session.commit()
        
        # 4. Link Students to ClassSection using legacy matching
        for u in User.query.filter(User.role == 'student', User.class_section_id == None).all():
            if u.institution_id:
                all_classes = ClassSection.query.filter_by(institution_id=u.institution_id).all()
                for c in all_classes:
                    if (normalize_dept(c.department) == normalize_dept(u.department_or_class) and
                        normalize_year(c.year) == normalize_year(u.year) and
                        normalize_section(c.section) == normalize_section(u.section)):
                        u.class_section_id = c.id
                        break
            if not u.class_section_id:
                print(f"[MIGRATION WARNING] Student ID {u.id} ({u.name}) has unresolved class section mapping.")
        db.session.commit()
        
        # 5. Link AttendanceSessions to ClassSection
        for sess in AttendanceSession.query.filter(AttendanceSession.class_section_id == None).all():
            c_sections = ClassSection.query.filter_by(
                department=sess.department,
                year=sess.year,
                section=sess.section
            ).all()
            if len(c_sections) == 1:
                sess.class_section_id = c_sections[0].id
            elif len(c_sections) > 1:
                print(f"[MIGRATION WARNING] AttendanceSession ID {sess.id} is ambiguous and could not be resolved.")
        db.session.commit()

        # 6. Link NotificationLogs to ClassSection
        for log in NotificationLog.query.filter(NotificationLog.class_section_id == None).all():
            cs_id = None
            if log.student_id:
                student = User.query.get(log.student_id)
                if student and student.class_section_id:
                    cs_id = student.class_section_id
            if not cs_id and log.advisor_id:
                advisor_class = ClassSection.query.filter_by(advisor_id=log.advisor_id).first()
                if advisor_class:
                    cs_id = advisor_class.id
            if cs_id:
                log.class_section_id = cs_id
            else:
                print(f"[MIGRATION WARNING] NotificationLog ID {log.id} has unresolved class section ownership.")
        db.session.commit()
        
    # Start scheduler only inside main process (to avoid duplicates in Flask debug reloader)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        import threading
        t = threading.Thread(target=run_attendance_scheduler, args=(app,), daemon=True)
        t.start()
        
    # Run the server on all interfaces, port 5000 (HTTP)
    app.run(host='0.0.0.0', port=5000, debug=True)