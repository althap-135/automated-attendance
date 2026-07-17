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

def resolve_current_class_section(user):
    """
    Resolves the ClassSection for the user (Teacher/Advisor).
    1. Resolves by advisor_id.
    2. Resolves by advisor_email.
    3. If not mapped, creates a ClassSection record.
    """
    if not user:
        return None
        
    from models import ClassSection
    from sqlalchemy import func
    
    # Resolve by advisor_id
    cs = ClassSection.query.filter_by(advisor_id=user.id).first()
    
    # Resolve by advisor_email (case-insensitive)
    if not cs and user.email:
        cs = ClassSection.query.filter(func.lower(ClassSection.advisor_email) == user.email.strip().lower()).first()
        if cs:
            cs.advisor_id = user.id
            db.session.commit()
            
    # Create dynamically if not found
    if not cs:
        try:
            cs = ClassSection(
                advisor_name=user.name,
                advisor_email=user.email,
                advisor_phone=user.mobile_number or "",
                advisor_id=user.id,
                institution_id=user.institution_id,
                institution_name=user.institution_name,
                institution_type=user.institution_type
            )
            db.session.add(cs)
            db.session.commit()
        except Exception:
            db.session.rollback()
            cs = ClassSection.query.filter_by(advisor_id=user.id).first()
            
    return cs

def get_students_for_class(user, class_section):
    """
    Queries and returns students who belong to the given ClassSection (matched strictly by class_section_id).
    Unresolved students (NULL class_section_id) are not returned.
    """
    if not class_section:
        return []
        
    from models import User
    
    # Query strictly by class_section_id
    students = User.query.filter_by(
        role='student',
        class_section_id=class_section.id
    ).all()
            
    return students

# ----------------- ROUTE PROTECTION MIDDLEWARE -----------------
@app.before_request
def require_login():
    # Endpoints allowed without authentication
    allowed_routes = ['login', 'register', 'api_login', 'api_register', 'static']
    # Specific API paths allowed without session
    allowed_apis = ['/api/attendance/scan']
    # Setup wizard API paths (require login but not setup completion)
    setup_api_prefixes = ['/api/setup/']
    
    if request.endpoint in allowed_routes or any(request.path.startswith(api) for api in allowed_apis):
        return
        
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': 'Authentication required.'}), 401
        return redirect(url_for('login'))

    user = User.query.get(session.get('user_id'))
    if not user:
        session.clear()
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': 'Session invalid.'}), 401
        return redirect(url_for('login'))

# ----------------- AUTHENTICATION ROUTES -----------------

@app.route('/login')
def login():
    if session.get('logged_in'):
        return redirect(url_for('home'))
    return render_template('login.html', page_type='login')

@app.route('/register')
def register():
    if session.get('logged_in'):
        return redirect(url_for('home'))
    return render_template('login.html', page_type='register')

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json() or request.form
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')
    institution_type = data.get('institution_type', '') # "College" or "School"
    institution_name = data.get('institution_name', '').strip()
    dept_or_class = data.get('department_or_class', '').strip()
    year = data.get('year', '').strip()
    section = data.get('section', '').strip()
    role = 'teacher'
    
    if not name or not email or not password or not institution_type or not institution_name or not dept_or_class or not section:
        return jsonify({'success': False, 'message': 'All required fields must be filled.'}), 400
        
    if password != confirm_password:
        return jsonify({'success': False, 'message': 'Passwords do not match.'}), 400
        
    # Check if user already exists
    existing = User.query.filter_by(email=email).first()
    if existing:
        return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
        
    hashed_password = generate_password_hash(password)
    
    new_user = User(
        name=name,
        email=email,
        password=hashed_password,
        institution_type=institution_type,
        institution_name=institution_name,
        department_or_class=dept_or_class,
        year=year if institution_type == 'College' else None,
        section=section,
        role=role
    )
    db.session.add(new_user)
    db.session.commit()
    
    # Establish session
    session['logged_in'] = True
    session['user_id'] = new_user.id
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
        
    # Clear any previous session residue
    session.clear()
    
    # Check/bind institution_id if NULL
    if not user.institution_id and user.institution_name:
        from models import Institution
        inst = Institution.query.filter_by(name=user.institution_name.strip()).first()
        if not inst:
            inst = Institution(name=user.institution_name.strip(), institution_type=user.institution_type or "College")
            db.session.add(inst)
            db.session.commit()
        user.institution_id = inst.id
        db.session.commit()
        
    # Set session keys
    session['logged_in'] = True
    session['user_id'] = user.id
    session['user_role'] = user.role
    session['institution_id'] = user.institution_id
    
    # Compatibility keys
    session['teacher_name'] = user.name
    session['teacher_id'] = user.email
    
    # Auto-resolve and set active class section
    cs = resolve_active_class_section(user)
    if cs:
        session['active_class_section_id'] = cs.id
        
    return jsonify({'success': True, 'message': f'Welcome back, {user.name}!'})


# ----------------- PAGE ROUTES -----------------

@app.route('/')
def home():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    # Filter students matching the class-specific context (role, creator_id)
    students_query = User.query.filter_by(role='student', creator_id=user.id)
    class_total_students = students_query.count()
    student_ids = [s.id for s in students_query.all()]
    
    today = date.today()
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
    if class_total_students > 0:
        attendance_rate = (today_present / class_total_students) * 100
        
    # Calculate overall attendance rate
    overall_rate = 0.0
    if student_ids:
        total_records = Attendance.query.filter(Attendance.user_id.in_(student_ids)).count()
        if total_records > 0:
            present_records = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.status == 'Present').count()
            overall_rate = (present_records / total_records) * 100
            
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(5).all()
    
    return render_template(
        'home.html',
        total_students=class_total_students,
        today_present=today_present,
        today_absent=effective_absent,
        unmarked_count=unmarked_count,
        attendance_rate=round(attendance_rate, 1),
        overall_rate=round(overall_rate, 1),
        recent_logs=recent_logs
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
    if not user or user.role != 'teacher':
        return "Access denied. Only teachers can access this page.", 403
        
    if user.role == 'teacher' and not session.get('teacher_face_verified'):
        return redirect(url_for('teacher_auth_page'))
        
    today = date.today()
    session_status = "Draft"
    
    # Resolve ClassSection
    cs = resolve_current_class_section(user)
    if cs:
        daily_sess = get_or_create_daily_session(user.department_or_class, user.year or "", user.section, today, cs.id)
        session_status = daily_sess.status
        students = get_students_for_class(user, cs)
        
        # Include all students created by this teacher
        created_students = User.query.filter_by(role='student', creator_id=user.id).all()
        student_ids = {s.id for s in students}
        for s in created_students:
            if s.id not in student_ids:
                students.append(s)
    else:
        students = []
    
    attendance_map = {a.user_id: a for a in Attendance.query.filter_by(date=today).all()}
    
    student_list = []
    for s in students:
        record = attendance_map.get(s.id)
        status = record.status if record else "Not Marked"
        marked_by = record.marked_by if record else "N/A"
        time_str = record.time.strftime('%I:%M %p') if (record and record.time) else "N/A"
        
        student_list.append({
            'id': s.id,
            'name': s.name,
            'register_number': s.email,
            'department': s.department_or_class,
            'year': s.year or 'N/A',
            'status': status,
            'time': time_str,
            'marked_by': marked_by
        })
        
    return render_template('correction.html', students=student_list, today_str=today.strftime('%Y-%m-%d'), session_status=session_status)

@app.route('/search')
def search_page():
    return render_template('search.html')

@app.route('/student/<register_number>')
def student_details(register_number):
    student = User.query.filter_by(role='student', email=register_number).first_or_404()
    
    if student.institution_id != session.get('institution_id'):
        return "Access denied. Institution mismatch.", 403
    
    # Calculate stats
    total_records = Attendance.query.filter_by(user_id=student.id).count()
    present_records = Attendance.query.filter_by(user_id=student.id, status='Present').count()
    absent_records = Attendance.query.filter_by(user_id=student.id, status='Absent').count()
    
    percentage = 100.0
    if total_records > 0:
        percentage = (present_records / total_records) * 100
        
    # Get student's history
    history_records = Attendance.query.filter_by(user_id=student.id).order_by(Attendance.date.desc()).all()
    
    return render_template(
        'details.html',
        student=student,
        total_days=total_records,
        present_days=present_records,
        absent_days=absent_records,
        percentage=round(percentage, 1),
        history=history_records
    )

@app.route('/history')
def history_page():
    user_id = session.get('user_id')
    db_records = Attendance.query.join(User).filter(User.creator_id == user_id).order_by(Attendance.date.desc(), Attendance.time.desc()).all()
    audit_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).all()
    
    return render_template('history.html', records=db_records, audit_logs=audit_logs)

@app.route('/analytics')
def analytics_page():
    return render_template('analytics.html')

@app.route('/settings')
def settings_page():
    current_user_id = session.get('user_id')
    curr_user = User.query.get(current_user_id) if current_user_id else None
    
    if curr_user:
        inst_id = curr_user.institution_id
        students = User.query.filter_by(role='student', creator_id=curr_user.id).all()
        teachers = User.query.filter_by(role='teacher', creator_id=curr_user.id).all()
        
        from models import ClassSection
        class_sections = ClassSection.query.filter_by(institution_id=inst_id).all()
        class_ids = [cs.id for cs in class_sections]
        student_ids = [s.id for s in students]
        teacher_ids = [t.id for t in teachers]
        
        notification_logs = NotificationLog.query.filter(
            (NotificationLog.class_section_id.in_(class_ids)) |
            (NotificationLog.student_id.in_(student_ids)) |
            (NotificationLog.advisor_id.in_(teacher_ids))
        ).order_by(NotificationLog.timestamp.desc()).all()
        
        # Scoped Counts
        total_teachers = len(teachers)
        total_students = len(students)
        total_users = total_teachers + total_students
        
        faces_registered = User.query.filter(
            User.creator_id == curr_user.id,
            User.role.in_(['teacher', 'student']),
            User.face_registered == True
        ).count()
        
        pending_registration = User.query.filter(
            User.creator_id == curr_user.id,
            User.role.in_(['teacher', 'student']),
            User.face_registered == False
        ).count()
    else:
        students = []
        teachers = []
        class_sections = []
        notification_logs = []
        total_teachers = 0
        total_students = 0
        total_users = 0
        faces_registered = 0
        pending_registration = 0
        
    # Load current AI configuration
    ai_config = load_ai_config()
    raw_key = ai_config.get('api_key', '')
    masked_key = ''
    if raw_key:
        masked_key = raw_key[:6] + '*' * max(0, len(raw_key) - 10) + raw_key[-4:] if len(raw_key) > 10 else '******'
        
    return render_template(
        'settings.html', 
        students=students, 
        teachers=teachers, 
        class_sections=class_sections,
        notification_logs=notification_logs,
        ai_provider=ai_config.get('provider', 'openai'),
        ai_key=masked_key,
        total_teachers=total_teachers,
        total_students=total_students,
        total_users=total_users,
        faces_registered=faces_registered,
        pending_registration=pending_registration
    )



# ----------------- WEB APIs -----------------

@app.route('/api/attendance/scan', methods=['POST'])
def api_scan_face():
    data = request.get_json()
    if not data or 'image' not in data or 'user_type' not in data:
        return jsonify({'success': False, 'message': 'Invalid parameters.'}), 400
        
    user_type = data['user_type'] # 'student' or 'teacher'
    img_bgr = decode_base64_image(data['image'])
    
    if img_bgr is None:
        return jsonify({'success': False, 'message': 'Failed to decode image.'}), 400
        
    predictions = FaceService.predict_face(img_bgr, user_type)
    
    if not predictions:
        if user_type == 'teacher':
            session['teacher_face_verified'] = False
        return jsonify({'success': False, 'message': 'No face detected or model not trained.'})
        
    # Process predictions, look for a match below the threshold
    match = None
    threshold = app.config['FACE_RECOGNITION_THRESHOLD']
    
    # Sort by confidence (LBPH distance - lower is better)
    predictions = sorted(predictions, key=lambda x: x['confidence'])
    best_pred = predictions[0]
    
    if best_pred['confidence'] <= threshold:
        match = best_pred
        
    if not match:
        if user_type == 'teacher':
            session['teacher_face_verified'] = False
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
        
        # Check if duplicate attendance today
        existing = Attendance.query.filter_by(user_id=student.id, date=today).first()
        
        if existing:
            if existing.status == 'Present':
                return jsonify({
                    'success': True,
                    'already_marked': True,
                    'message': f"Attendance already marked for {student.name}.",
                    'student': student.to_dict(),
                    'confidence': conf,
                    'box': box
                })
            else:
                # If marked absent, update to present
                existing.status = 'Present'
                existing.time = now_time
                existing.marked_by = "Face Recognition"
                db.session.commit()
                return jsonify({
                    'success': True,
                    'message': f"Attendance updated to Present for {student.name}.",
                    'student': student.to_dict(),
                    'confidence': conf,
                    'box': box
                })
                
        # Register new present attendance record
        new_attendance = Attendance(
            user_id=student.id,
            date=today,
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
        current_logged_in_id = session.get('user_id')
        if not current_logged_in_id:
            session['teacher_face_verified'] = False
            return jsonify({'success': False, 'message': 'Authentication required. Please log in first.'}), 401
            
        logged_in_user = User.query.filter_by(id=current_logged_in_id, role='teacher').first()
        if not logged_in_user:
            session.clear()
            return jsonify({'success': False, 'message': 'Session user not found or invalid role.'}), 401
            
        teacher = User.query.filter_by(id=matched_id, role='teacher').first()
        if not teacher:
            session['teacher_face_verified'] = False
            return jsonify({'success': False, 'message': 'Teacher record not found in database.'})
            
        # Verify matched teacher is the logged-in user and preserve institution isolation
        if teacher.id != logged_in_user.id or teacher.institution_id != logged_in_user.institution_id:
            session['teacher_face_verified'] = False
            return jsonify({'success': False, 'message': 'Face authentication mismatch. You are not the logged-in teacher.'}), 403
            
        # Set face verified status for the logged-in user (never modify session['user_id'] here)
        session['teacher_face_verified'] = True
        
        return jsonify({
            'success': True,
            'message': f"Authentication successful. Welcome, {teacher.name}!",
            'teacher': teacher.to_dict(),
            'confidence': conf,
            'box': box
        })

    return jsonify({'success': False, 'message': 'Invalid user type.'})

@app.route('/api/attendance/correct', methods=['POST'])
def api_correct_attendance():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401

    # Staff Face Auth is required for finalization
    if not session.get('teacher_face_verified'):
        return jsonify({'success': False, 'message': 'Staff Face Authentication required. Please perform face authentication before approving attendance.'}), 403
    
    # Staff Face Auth is required for this protected operation
    if not session.get('teacher_face_verified'):
        return jsonify({'success': False, 'message': 'Staff Face Authentication required. Please scan your face at /teacher/auth before performing this action.'}), 403
        
    user = User.query.get(session.get('user_id'))
    if not user or (user.role == 'teacher' and not session.get('teacher_face_verified')):
        return jsonify({'success': False, 'message': 'Access denied. Successful face recognition scan required.'}), 403
        
    data = request.get_json()
    if not data or 'student_id' not in data or 'date' not in data or 'new_status' not in data or 'reason' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    student_id = data['student_id']
    date_str = data['date'] # YYYY-MM-DD
    new_status = data['new_status'] # "Present" or "Absent"
    reason = data['reason']
    
    # Reason check
    allowed_reasons = ["Medical Leave", "Late Arrival", "Technical Error", "Official Permission", "Other"]
    if reason not in allowed_reasons:
        return jsonify({'success': False, 'message': 'Invalid reason selected.'}), 400
        
    # Check modification rules: Present -> Absent is NOT allowed
    if new_status == 'Absent':
        return jsonify({'success': False, 'message': 'Rule Error: Changing status from Present to Absent is not allowed.'}), 403
        
    # Convert date
    try:
        record_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400
        
    # Validate 24-hour limit
    today = date.today()
    # Check if record_date is within today or yesterday
    if (today - record_date) > timedelta(days=1):
        return jsonify({'success': False, 'message': 'Rule Error: Attendance modification is only allowed within 24 hours.'}), 403
        
    student = User.query.filter_by(id=student_id, role='student').first()
    if not student:
        return jsonify({'success': False, 'message': 'Student not found.'}), 404
    
    # Creator isolation — only accessible by creator
    if student.creator_id != user.id:
        return jsonify({'success': False, 'message': 'Access Denied: You do not have permission for this student.'}), 403
        
    # Fetch existing record or create one if it doesn't exist
    record = Attendance.query.filter_by(user_id=student_id, date=record_date).first()
    prev_status = "Not Marked"
    
    now_time = datetime.now().time()
    
    if record:
        prev_status = record.status
        if prev_status == 'Present':
            return jsonify({'success': False, 'message': 'Already Present. No modification needed.'}), 400
            
        # Update
        record.status = 'Present'
        record.time = now_time
        record.marked_by = f"Teacher: {session.get('teacher_name')} ({session.get('teacher_id')})"
    else:
        # Create new — use user_id (correct FK column name)
        record = Attendance(
            user_id=student_id,
            date=record_date,
            time=now_time,
            status='Present',
            marked_by=f"Teacher: {session.get('teacher_name')} ({session.get('teacher_id')})"
        )
        db.session.add(record)
        
    # Create Audit Log
    audit = AuditLog(
        student_name=student.name,
        register_number=student.reg_number or student.email,
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


@app.route('/api/attendance/finalize', methods=['POST'])
def api_finalize_attendance():
    """
    Teacher closes daily attendance.
    Marks all unmarked students as Absent for the specified date (defaults to today).
    Triggers parent WhatsApp alerts for absentees.
    Sends summary report to the teacher.
    """
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401

    # Staff Face Auth is required for finalization
    if not session.get('teacher_face_verified'):
        return jsonify({'success': False, 'message': 'Staff Face Authentication required. Please perform face authentication before approving attendance.'}), 403
    
    # Staff Face Auth is required for this protected operation
    if not session.get('teacher_face_verified'):
        return jsonify({'success': False, 'message': 'Staff Face Authentication required. Please scan your face at /teacher/auth before performing this action.'}), 403
        
    data = request.get_json() or {}
    date_str = data.get('date', date.today().strftime('%Y-%m-%d'))
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400
        
    students = Student.query.all()
    attendance_map = {a.student_id: a for a in Attendance.query.filter_by(date=target_date).all()}
    
    absent_count = 0
    present_count = 0
    new_absent_list = []
    
    now_time = datetime.now().time()
    
    for s in students:
        record = attendance_map.get(s.id)
        if not record:
            # Not marked yet today, automatically mark as Absent
            record = Attendance(
                student_id=s.id,
                date=target_date,
                time=now_time,
                status='Absent',
                marked_by='System Finalize'
            )
            db.session.add(record)
            absent_count += 1
            new_absent_list.append(s)
        elif record.status == 'Absent':
            absent_count += 1
        elif record.status == 'Present':
            present_count += 1
            
    db.session.commit()
    
    # 1. Trigger Parent WhatsApp alerts for absent students
    # Send for all absent students on this date
    all_absents = User.query.filter_by(role='student').join(Attendance).filter(
        Attendance.date == target_date,
        Attendance.status == 'Absent'
    ).all()
    
    notifications_sent = 0
    for s in all_absents:
        NotificationService.send_absent_notification(s, date_str)
        notifications_sent += 1
        
    # 2. Trigger Teacher WhatsApp Summary
    teacher_record = User.query.filter_by(id=session.get('user_id')).first()
    if teacher_record:
        stats = {
            'total': len(students),
            'present': present_count,
            'absent': absent_count,
            'percentage': (present_count / len(students) * 100) if len(students) > 0 else 0
        }
        NotificationService.send_teacher_summary_notification(teacher_record, date_str, stats)
        
    return jsonify({
        'success': True,
        'message': f"Attendance finalized. {notifications_sent} parent alerts triggered, and summary report sent to teacher.",
        'stats': {
            'total': len(students),
            'present': present_count,
            'absent': absent_count,
            'rate': round((present_count / len(students) * 100) if len(students) > 0 else 0, 1)
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
        
    students_query = User.query.filter_by(role='student', creator_id=user.id)
    total_students = students_query.count()
    student_ids = [s.id for s in students_query.all()]
    
    today = date.today()
    present_students = []
    if student_ids:
        today_present = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Present').count()
        today_absent = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Absent').count()
        recorded_ids = [a.user_id for a in Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today).all()]
        unmarked_count = len(set(student_ids) - set(recorded_ids))
        
        # Query present students details without duplicates
        present_attendance = Attendance.query.filter(
            Attendance.user_id.in_(student_ids),
            Attendance.date == today,
            Attendance.status == 'Present'
        ).all()
        seen_student_ids = set()
        for att in present_attendance:
            if att.user_id not in seen_student_ids:
                seen_student_ids.add(att.user_id)
                st = att.user
                if st:
                    present_students.append({
                        'name': st.name,
                        'reg_number': st.reg_number or '',
                        'face_registered': st.face_registered
                    })
    else:
        today_present = 0
        today_absent = 0
        unmarked_count = 0
        
    effective_absent = today_absent + unmarked_count
    attendance_rate = 0.0
    if total_students > 0:
        attendance_rate = (today_present / total_students) * 100
        
    overall_rate = 0.0
    if student_ids:
        total_records = Attendance.query.filter(Attendance.user_id.in_(student_ids)).count()
        if total_records > 0:
            present_records = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.status == 'Present').count()
            overall_rate = (present_records / total_records) * 100
            
    return jsonify({
        'success': True,
        'today_present': today_present,
        'today_absent': effective_absent,
        'attendance_rate': round(attendance_rate, 1),
        'overall_rate': round(overall_rate, 1),
        'present_students': present_students
    })


@app.route('/api/student/search', methods=['GET'])
def api_search_students():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
        
    # Search by register number or name
    results = User.query.filter(
        User.role == 'student',
        User.creator_id == session.get('user_id')
    ).filter(
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
        
    # Gather Database Context
    today = date.today()
    user_id = session.get('user_id')
    students = User.query.filter_by(role='student', creator_id=user_id).all()
    student_ids = [s.id for s in students]
    total_students = len(students)
    
    if student_ids:
        today_present = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Present').count()
        today_absent = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today, Attendance.status == 'Absent').count()
        recorded_ids = [a.user_id for a in Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == today).all()]
        unmarked_count = len(set(student_ids) - set(recorded_ids))
    else:
        today_present = 0
        today_absent = 0
        recorded_ids = []
        unmarked_count = 0
        
    effective_absent = today_absent + unmarked_count
    
    attendance_rate = 0.0
    if total_students > 0:
        attendance_rate = round((today_present / total_students) * 100, 1)
        
    # Get compact student list context
    students_list = []
    for s in students:
        students_list.append(f"- Name: {s.name}, Reg: {s.email}, Dept: {s.department_or_class}, Year: {s.year or 'N/A'}")
    students_context = "\n".join(students_list) if students_list else "No students registered yet."
    
    # Get compact today's attendance logs
    today_records = Attendance.query.filter_by(date=today).all()
    attendance_list = []
    for a in today_records:
        student_name = a.user.name if a.user else "Unknown"
        attendance_list.append(f"- Student: {student_name}, Status: {a.status}, Time: {a.time.strftime('%H:%M') if a.time else 'N/A'}, Marked By: {a.marked_by}")
    attendance_context = "\n".join(attendance_list) if attendance_list else "No attendance logged today yet."
    
    # Get recent audit logs (last 5 modifications)
    audit_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(5).all()
    audit_list = []
    for log in audit_logs:
        audit_list.append(f"- [{log.timestamp.strftime('%H:%M')}] Student: {log.student_name}, Teacher: {log.teacher_name}, Prev: {log.previous_status}, New: {log.updated_status}, Reason: {log.reason}")
    audit_context = "\n".join(audit_list) if audit_list else "No modifications in audit log."

    # Load AI configuration key
    ai_config = load_ai_config()
    provider = ai_config.get('provider', 'openai')
    api_key = ai_config.get('api_key', '')
    
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
   - Today's Attendance
   - Attendance Percentage
   - Quick Access Cards
3. Student Management
   - Add Student
   - Edit Student
   - Delete Student
   - Search Student by Register Number
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

- TODAY'S ATTENDANCE LOGS:
{attendance_context}

- RECENT AUDIT LOGS:
{audit_context}

INSTRUCTIONS:
- Always explain step-by-step navigation using arrows (e.g. Login → Dashboard → ...).
- Explain how each feature works when asked.
- Guide users like a real application support assistant.
- Answer only questions related to this attendance system.
- If a feature does not exist, say:
  "This feature is not available in the current version of the application."
- Keep your answers clear, concise, structured, and helpful.

EXAMPLES:
User: Where can I view attendance reports?
Assistant:
Login → Dashboard → Attendance Records → Attendance Reports.

User: How do I mark attendance?
Assistant:
Login → Face Recognition Attendance → Open Camera → Scan Face → Attendance will be marked automatically after successful verification.

User: How do I correct attendance?
Assistant:
Login → Attendance Correction → Teacher Authentication → Select Student → Enter Reason → Save Changes.
"""


    # Try Gemini API if configured
    if provider == 'gemini' and api_key:
        try:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            headers = {'Content-Type': 'application/json'}
            payload = {
                "contents": [{
                    "parts": [{
                        "text": f"{system_prompt}\n\nUser Question: {user_message}"
                    }]
                }],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 400
                }
            }
            res = requests.post(gemini_url, json=payload, headers=headers, timeout=8)
            if res.status_code == 200:
                reply = res.json()['candidates'][0]['content']['parts'][0]['text']
                return jsonify({'success': True, 'reply': reply, 'source': 'Gemini API'})
            else:
                print(f"Gemini API returned error {res.status_code}: {res.text}")
        except Exception as e:
            print(f"Gemini API request failed: {e}")

    # Try OpenAI API if configured (or use environment key if provider is openai)
    active_openai_key = api_key if (provider == 'openai' and api_key) else os.environ.get('OPENAI_API_KEY')
    if active_openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=active_openai_key)
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=350,
                temperature=0.3
            )
            ai_reply = response.choices[0].message.content.strip()
            return jsonify({'success': True, 'reply': ai_reply, 'source': 'OpenAI GPT'})
        except Exception as e:
            print(f"OpenAI Chat Completion failed: {e}")

            
    # Smart Local Parser Fallback (Offline/No-API-Key Mode)
    query_lower = user_message.lower()
    
    if 'absent' in query_lower:
        absents = []
        for s in students:
            is_present = any(a.user_id == s.id and a.status == 'Present' for a in today_records)
            if not is_present:
                absents.append(f"{s.name} ({s.email})")
                
        if not absents:
            reply = "No students are absent today! Everyone is marked Present."
        else:
            reply = "Here are the students absent or unmarked today:\n" + "\n".join([f"- {name}" for name in absents])
            
    # Check present query
    elif 'present' in query_lower:
        presents = [f"{a.user.name if a.user else 'Unknown'} ({a.user.email if a.user else 'N/A'}) at {a.time.strftime('%I:%M %p') if a.time else 'N/A'}" for a in today_records if a.status == 'Present']
        if not presents:
            reply = "No students are marked Present yet today."
        else:
            reply = "Here are the students marked Present today:\n" + "\n".join([f"- {name}" for name in presents])
            
    # Check stats query
    elif 'stat' in query_lower or 'percentage' in query_lower or 'rate' in query_lower or 'count' in query_lower:
        reply = (
            f"Here are the current stats for today ({today.strftime('%Y-%m-%d')}):\n"
            f"- Total Registered Students: {total_students}\n"
            f"- Present: {today_present}\n"
            f"- Absent/Unmarked: {effective_absent}\n"
            f"- Daily Attendance Rate: {attendance_rate}%"
        )
        
    # Check register or enroll query
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
            "- **Allowed status changes**: ONLY **Absent &rarr; Present** is allowed. Present &rarr; Absent is strictly blocked by system constraints.\n"
            "- **Time Limit**: Changes can only be made within **24 hours** of the attendance date (today or yesterday).\n"
            "- **Reason Required**: Teachers must select a valid reason (Medical Leave, Late Arrival, Technical Error, Official Permission, Other) and click confirm.\n"
            "- **Logging**: All changes are permanently recorded in the secure **Audit Trail** viewable on the History page."
        )
 
    # Check finalize query
    elif 'finalize' in query_lower or 'close' in query_lower:
        reply = (
            "### Finalizing Attendance:\n"
            "- Finalizing attendance is done by teachers clicking **Finalize Today's Attendance** on the Teacher Dashboard.\n"
            "- **Automatic Absent marking**: Any student who has not scanned present is automatically marked as **Absent**.\n"
            "- **Parent Alerts**: A simulated WhatsApp notification is automatically sent to the parents of all absent students.\n"
            "- **Teacher Summary**: A summary report of present/absent rates is compiled and sent to the teacher's WhatsApp/email."
        )
        
    # Check camera or scan query
    elif 'scanner' in query_lower or 'camera' in query_lower or 'scan' in query_lower:
        reply = (
            "### Webcam Face Scanning:\n"
            "- **Students**: Go to the **Student Scan** page, click **Start Scanner**, and stand in front of the camera. The system draws green tracking boxes and logs present state in real-time.\n"
            "- **Teachers**: Go to the **Teacher Portal**, click **Start Auth Scan** to facial-login. Once recognized, it unlocks the correction dashboard."
        )
        
    # Check search query
    else:
        matched_student = None
        for s in students:
            if s.name.lower() in query_lower or s.email.lower() in query_lower:
                matched_student = s
                break
                
        if matched_student:
            tot = Attendance.query.filter_by(user_id=matched_student.id).count()
            pres = Attendance.query.filter_by(user_id=matched_student.id, status='Present').count()
            pct = (pres / tot * 100) if tot > 0 else 100.0
            
            reply = (
                f"I found a record matching **{matched_student.name}**:\n"
                f"- Register Number (Email): {matched_student.email}\n"
                f"- Department: {matched_student.department_or_class} ({matched_student.year or 'N/A'})\n"
                f"- Overall Attendance Rate: {pct:.1f}% ({pres}/{tot} days)"
            )
        else:
            reply = (
                "Hi! I am the NexAttend AI Local Helper. I couldn't reach the online LLM, but I can still answer basic questions about attendance stats and records!\n\n"
                "Try asking me:\n"
                "- *'Who is absent today?'*\n"
                "- *'Who is present today?'*\n"
                "- *'Show stats'* or *'What is the attendance rate?'*\n"
                "- *'Search [student name or register number]'*\n"
                "- *'How to register a student?'*\n"
                "- *'What are the correction rules?'*"
            )
            
    return jsonify({'success': True, 'reply': reply, 'source': 'Local Parser'})



# ----------------- SETTINGS & FACE REGISTRATION APIs -----------------

@app.route('/api/settings/save_ai_config', methods=['POST'])
def api_save_ai_config():
    """Saves AI provider selection and API keys to local config."""
    data = request.get_json()
    if not data or 'provider' not in data or 'api_key' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    provider = data['provider']
    api_key = data['api_key'].strip()
    
    if provider not in ['openai', 'gemini']:
        return jsonify({'success': False, 'message': 'Invalid AI provider.'}), 400
        
    # If the key contains asterisks, they did not edit the masked value, so preserve the existing key
    if '*' in api_key or api_key == '******':
        current_config = load_ai_config()
        api_key = current_config.get('api_key', '')
        
    if not api_key:
        return jsonify({'success': False, 'message': 'API Key cannot be empty.'}), 400
        
    success = save_ai_config(provider, api_key)
    if success:
        return jsonify({'success': True, 'message': f'AI Assistant config updated to {provider.upper()}!'})
    else:
        return jsonify({'success': False, 'message': 'Failed to save configuration.'}), 500

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
        parent_phone = data.get('parent_phone', '').strip()
        password_plain = data.get('password', 'password123').strip() or 'password123'
        
        # Determine department, year, and section directly from input
        dept = data.get('department', '').strip()
        year = data.get('year', '').strip() if inst_type == 'College' else ""
        section = data.get('section', '').strip()
        
        # Fallback to class_id if provided (for backward compatibility with existing tests)
        class_id = data.get('class_id')
        if class_id and not (dept or year or section):
            from models import ClassSection
            try:
                cs_test = ClassSection.query.get(int(class_id))
                if cs_test:
                    # Enforce IDOR protection: check access
                    if not verify_class_access(creator, cs_test):
                        return jsonify({'success': False, 'message': 'Access Denied: You do not have permission for this class section.'}), 403
                    dept = cs_test.department
                    year = cs_test.year or ""
                    section = cs_test.section
            except (ValueError, TypeError):
                pass
                
        # If still empty, fall back to creator's active class section details
        if not (dept or section):
            cs = resolve_active_class_section(creator)
            if cs:
                dept = cs.department
                year = cs.year or ""
                section = cs.section
                
        if not reg_num or not dept or (inst_type == 'College' and not year) or not section or not parent_phone:
            return jsonify({'success': False, 'message': 'Missing required student fields (Register Number, Dept, Section, Parent Mobile Number are required, and Year for College).'}), 400
            
        import re
        if not re.match(r'^\+\d{10,15}$', parent_phone):
            return jsonify({'success': False, 'message': 'Parent Mobile Number must be in E.164 international format (e.g. +919876543210).'}), 400
            
        # Check duplicate register number
        existing_reg = User.query.filter_by(reg_number=reg_num).first()
        if existing_reg:
            return jsonify({'success': False, 'message': 'This Register Number is already registered'}), 400
            
        # Check duplicate email
        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
            
        # Resolve creator's advisor mapping (ClassSection)
        cs = resolve_current_class_section(creator)
                    
        new_student = User(
            name=name,
            email=email,
            password=generate_password_hash(password_plain),
            institution_type=inst_type,
            institution_name=inst_name,
            institution_id=creator.institution_id,
            class_section_id=cs.id if cs else None,
            department_or_class=dept,
            year=year if inst_type == 'College' else None,
            section=section,
            role=role,
            mobile_number=mobile_number or None,
            parent_phone=parent_phone,
            reg_number=reg_num,
            creator_id=creator.id
        )
        db.session.add(new_student)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Student {name} registered successfully.'})
        
    elif role == 'teacher':
        # Teachers can create teachers
        if creator.role != 'teacher':
            return jsonify({'success': False, 'message': 'Access Denied: Only Teachers can register teachers.'}), 403
            
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
            mobile_number=mobile_number or None,
            creator_id=creator.id
        )
        db.session.add(new_teacher)
        db.session.commit()
        
        # Send WhatsApp Welcome Message to the new staff member if they have a mobile
        is_advisor = data.get('is_advisor', False)
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
            'message': f'{"Advisor" if is_advisor else "Teacher"} {name} registered successfully.',
            'user_id': new_teacher.id,
            'is_advisor': is_advisor
        })

        
    return jsonify({'success': False, 'message': 'Invalid role.'}), 400

@app.route('/api/settings/set_admin_class', methods=['POST'])
def api_set_admin_class():
    user = get_current_user()
    if not user or user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    data = request.get_json() or {}
    class_id = data.get('class_id')
    if not class_id:
        return jsonify({'success': False, 'message': 'Missing class_id'}), 400
    from models import ClassSection
    cs = ClassSection.query.get(int(class_id))
    if not cs or cs.institution_id != user.institution_id:
        return jsonify({'success': False, 'message': 'Access Denied: Class section does not belong to your institution.'}), 403
    session['admin_selected_class_id'] = cs.id
    session['active_class_section_id'] = cs.id
    return jsonify({'success': True, 'message': f'Active class set to {cs.department} {cs.year} - {cs.section}.'})



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
        
    # Creator isolation / Self-enrollment permission check
    is_authorized = False
    if user.id == creator.id or user.creator_id == creator.id:
        is_authorized = True
    else:
        from models import ClassSection
        is_authorized = ClassSection.query.filter_by(
            advisor_email=user.email,
            institution_id=creator.institution_id
        ).first() is not None
        
    if not is_authorized:
        return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to enroll this face.'}), 403
            
    img_bgr = decode_base64_image(data['image'])
    if img_bgr is None:
        return jsonify({'success': False, 'message': 'Failed to decode image.'}), 400
        
    success, message = FaceService.save_face_snapshots(img_bgr, user_type, user_id, snapshot_index)
    if not success:
        return jsonify({'success': False, 'message': message})
        
    # If 5th snapshot, train the model
    if snapshot_index == 5:
        user.face_registered = True
        db.session.commit()
        
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
def api_delete_face():
    creator_id = session.get('user_id')
    creator = User.query.get(creator_id) if creator_id else None
    if not creator:
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    data = request.get_json()
    if not data or 'user_type' not in data or 'user_id' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    user_type = data['user_type']
    user_id = int(data['user_id'])
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
    # Creator isolation / Self-deletion permission check
    is_authorized = False
    if user.id == creator.id or user.creator_id == creator.id:
        is_authorized = True
    else:
        from models import ClassSection
        is_authorized = ClassSection.query.filter_by(
            advisor_email=user.email,
            institution_id=creator.institution_id
        ).first() is not None
        
    if not is_authorized:
        return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to delete this face.'}), 403
            
    if user_type == 'student':
        parent_dir = app.config['STUDENT_FACES_DIR']
    else:
        parent_dir = app.config['TEACHER_FACES_DIR']
        
    user.face_registered = False
    db.session.commit()
    
    # Delete images directory
    user_dir = os.path.join(parent_dir, str(user_id))
    if os.path.exists(user_dir):
        for file in os.listdir(user_dir):
            os.remove(os.path.join(user_dir, file))
        os.rmdir(user_dir)
        
    # Retrain model
    FaceService.train_model(user_type)
    return jsonify({'success': True, 'message': f"Facial record deleted for {user.name}."})

@app.route('/api/settings/delete_user', methods=['POST'])
def api_delete_user():
    """Completely deletes a student or teacher record from the database, including attendance and face files."""
    creator_id = session.get('user_id')
    creator = User.query.get(creator_id) if creator_id else None
    if not creator:
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    data = request.get_json()
    if not data or 'role' not in data or 'user_id' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    role = data['role']
    user_id = int(data['user_id'])
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
    # Prevent self-deletion
    if user.id == creator.id:
        return jsonify({'success': False, 'message': 'Access Denied: You cannot delete your own account.'}), 403

    # Creator isolation — only accessible by creator
    if user.creator_id != creator.id:
        return jsonify({'success': False, 'message': 'Access Denied: You do not have permission to delete this user.'}), 403

    # Creator must be a teacher
    if creator.role != 'teacher':
        return jsonify({'success': False, 'message': 'Access Denied.'}), 403

    # Cannot delete admin accounts (should not exist, but guard just in case)
    if user.role not in ['teacher', 'student']:
        return jsonify({'success': False, 'message': 'Access Denied: Invalid target role.'}), 403

    # If the target teacher is a mapped Advisor, clear the ClassSection advisor fields first
    if user.role == 'teacher':
        from models import ClassSection
        ClassSection.query.filter_by(advisor_id=user.id).update({
            'advisor_id': None,
            'advisor_name': None,
            'advisor_phone': None,
            'advisor_email': None
        })
        db.session.commit()
            
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
            
    # Delete related attendance entries if deleting a student
    if role == 'student':
        try:
            Attendance.query.filter_by(user_id=user_id).delete()
        except Exception as e:
            print(f"Error deleting attendance: {e}")
            
    # Delete user database object
    try:
        db.session.delete(user)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f"Database deletion error: {e}"}), 500
        
    # Retrain facial models
    try:
        FaceService.train_model(role)
    except Exception as e:
        print(f"Error retraining model: {e}")
        
    return jsonify({'success': True, 'message': f"User record for {user.name} and associated data deleted successfully."})


# ----------------- SETUP WIZARD -----------------


def get_current_user():
    from models import User
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = User.query.get(user_id)
    if user and not user.institution_id and user.institution_name:
        from models import Institution
        inst = Institution.query.filter_by(name=user.institution_name.strip()).first()
        if not inst:
            inst = Institution(name=user.institution_name.strip(), institution_type=user.institution_type or "College")
            db.session.add(inst)
            db.session.commit()
        user.institution_id = inst.id
        db.session.commit()
        session['institution_id'] = user.institution_id
    return user

def resolve_authorized_class_sections(user):
    """
    Returns all ClassSections the user is authorized to manage.
    Admin: all in their institution.
    Teacher/Advisor: only those mapped to their advisor_id.
    """
    from models import ClassSection
    if not user:
        return []
    # All teachers in the institution can see and manage all class sections
    return ClassSection.query.filter_by(institution_id=user.institution_id).all()

def resolve_active_class_section(user):
    """
    Resolves the active ClassSection for the user.
    """
    return resolve_current_class_section(user)

def verify_class_access(user, class_section):
    """
    Verifies that the user has permission to access the given ClassSection.
    """
    if not user or not class_section:
        return False
        
    if user.institution_id != class_section.institution_id:
        return False
    # Any teacher in the same institution can access any class section
    return True

def get_or_create_daily_session(dept, year, section, today_date, class_section_id=None):
    from models import AttendanceSession, ClassSection
    sess = AttendanceSession.query.filter_by(
        department=dept,
        year=year,
        section=section,
        attendance_date=today_date
    ).first()
    
    if not sess:
        sess = AttendanceSession(
            department=dept,
            year=year,
            section=section,
            attendance_date=today_date,
            status='Draft',
            class_section_id=class_section_id
        )
        db.session.add(sess)
        db.session.commit()
    return sess

def submit_attendance_session_to_advisor(session_id, user_id=None, is_automatic=False):
    """
    Core Step-1 attendance submission logic shared by manual and automatic flows.
    """
    from models import AttendanceSession, ClassSection, User, Attendance, NotificationLog
    
    sess_rec = AttendanceSession.query.get(session_id)
    if not sess_rec:
        return False, "Session not found."
        
    if sess_rec.status != 'Draft':
        return False, f"Cannot submit review for {sess_rec.status.lower()} session."
        
    # Find mapping
    mapping = None
    if sess_rec.class_section_id:
        mapping = ClassSection.query.get(sess_rec.class_section_id)
        
    if not mapping:
        from models import User
        advisor = User.query.filter_by(
            role='teacher',
            department_or_class=sess_rec.department,
            year=sess_rec.year,
            section=sess_rec.section
        ).first()
        if advisor:
            mapping = ClassSection.query.filter_by(advisor_id=advisor.id).first()
            
    # Query all students in this class section
    students = []
    if mapping:
        students = User.query.filter(
            (User.role == 'student') &
            (
                (User.class_section_id == mapping.id) |
                (
                    (User.department_or_class == sess_rec.department) &
                    (User.year == (sess_rec.year or "")) &
                    (User.section == sess_rec.section)
                )
            )
        ).all()
        
    today = sess_rec.attendance_date
    
    # Mark unmarked students as Absent
    for s in students:
        att = Attendance.query.filter_by(user_id=s.id, date=today).first()
        if not att:
            att = Attendance(
                user_id=s.id,
                date=today,
                time=datetime.now().time(),
                status='Absent',
                marked_by='System'
            )
            db.session.add(att)
            
    # Transition session status
    sess_rec.status = 'Submitted'
    sess_rec.is_automatic = is_automatic
    sess_rec.submitted_at = datetime.utcnow()
    if user_id:
        sess_rec.submitted_by = user_id
        
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return False, f"Database error: {e}"
        
    # Send WhatsApp notification to Class Advisor
    if mapping and mapping.advisor_phone:
        # Calculate counts
        student_ids = [s.id for s in students]
        present_count = Attendance.query.filter(
            Attendance.user_id.in_(student_ids),
            Attendance.date == today,
            Attendance.status == 'Present'
        ).count() if student_ids else 0
        absent_count = len(students) - present_count
        
        prefix = "⚠️ Automatic Attendance Review" if is_automatic else "📊 Attendance Review"
        message_body = (
            f"{prefix}\n\n"
            f"Class: {sess_rec.department} {sess_rec.year or ''} - Section {sess_rec.section}\n"
            f"Date: {today.strftime('%Y-%m-%d')}\n"
            f"Present: {present_count}\n"
            f"Absent: {absent_count}\n\n"
            f"Please log in and review the draft daily attendance report."
        )
        
        # Log notification
        notif_log = NotificationLog(
            type="Daily Attendance Review",
            recipient_contact=mapping.advisor_phone,
            message=message_body,
            status="Pending",
            timestamp=datetime.utcnow()
        )
        db.session.add(notif_log)
        db.session.commit()
        
        # Async Twilio Send
        import threading
        from flask import current_app
        def send_async(app_ctx, log_id, to_no, msg, name):
            with app_ctx:
                try:
                    NotificationService.send_whatsapp(
                        to_number=to_no,
                        message=msg,
                        recipient_name=name,
                        msg_type="Daily Attendance Review"
                    )
                    log_rec = NotificationLog.query.get(log_id)
                    if log_rec:
                        log_rec.status = "Sent"
                        db.session.commit()
                except Exception as ex:
                    log_rec = NotificationLog.query.get(log_id)
                    if log_rec:
                        log_rec.status = "Failed"
                        log_rec.error_details = str(ex)
                        db.session.commit()
                        
        threading.Thread(
            target=send_async,
            args=(current_app._get_current_object().app_context(), notif_log.id, mapping.advisor_phone, message_body, mapping.advisor_name),
            daemon=True
        ).start()
        
    return True, "Session submitted to advisor successfully."


@app.route('/api/settings/save_advisor_map', methods=['POST'])
def api_save_advisor_map():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    user = get_current_user()
    if not user or user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Access Denied: Only Teachers can manage advisor mappings.'}), 403
        
    data = request.get_json() or {}
    mapping_id = data.get('id')
    advisor_name = data.get('advisor_name', '').strip()
    advisor_email = data.get('advisor_email', '').strip()
    advisor_phone = data.get('advisor_phone', '').strip()
    password = data.get('password', '').strip()
    confirm_password = data.get('confirm_password', '').strip()
    
    if not advisor_name or not advisor_email or not advisor_phone:
        return jsonify({'success': False, 'message': 'Advisor Name, Email, and WhatsApp Phone are required.'}), 400
        
    if not mapping_id and (not password or not confirm_password):
        return jsonify({'success': False, 'message': 'Password and Confirm Password are required.'}), 400
        
    if password and password != confirm_password:
        return jsonify({'success': False, 'message': 'Passwords do not match.'}), 400
        
    import re
    if not re.match(r'^\+[1-9]\d{1,14}$', advisor_phone):
        return jsonify({'success': False, 'message': 'WhatsApp Number must be in E.164 format (e.g. +919876543210).'}), 400
        
    from models import ClassSection
    
    mapping = None
    if mapping_id:
        mapping = ClassSection.query.filter_by(id=mapping_id, institution_id=user.institution_id).first()
        
    if not mapping:
        mapping = ClassSection.query.filter_by(advisor_email=advisor_email, institution_id=user.institution_id).first()
        
    # Check if this email is already registered globally (to avoid UNIQUE constraint failure)
    existing_global_user = User.query.filter_by(email=advisor_email).first()
    if existing_global_user and existing_global_user.institution_id != user.institution_id:
        return jsonify({'success': False, 'message': 'This email ID is already registered under another institution.'}), 400
        
    advisor_user = User.query.filter_by(email=advisor_email, institution_id=user.institution_id).first()
    new_advisor_created = False
    from werkzeug.security import generate_password_hash
    if not advisor_user:
        advisor_user = User(
            name=advisor_name,
            email=advisor_email,
            password=generate_password_hash(password),
            institution_type=user.institution_type,
            institution_name=user.institution_name,
            institution_id=user.institution_id,
            role='teacher',
            mobile_number=advisor_phone or None,
            creator_id=user.id,
            department_or_class=user.department_or_class,
            year=user.year,
            section=user.section
        )
        db.session.add(advisor_user)
        db.session.flush()
        new_advisor_created = True
    else:
        advisor_user.name = advisor_name
        advisor_user.mobile_number = advisor_phone or None
        if password:
            advisor_user.password = generate_password_hash(password)
        db.session.commit()
        
    advisor_id = advisor_user.id
        
    if not mapping:
        mapping = ClassSection(
            advisor_name=advisor_name,
            advisor_email=advisor_email,
            advisor_phone=advisor_phone,
            advisor_id=advisor_id,
            institution_id=user.institution_id,
            institution_name=user.institution_name,
            institution_type=user.institution_type
        )
        db.session.add(mapping)
    else:
        mapping.advisor_name = advisor_name
        mapping.advisor_email = advisor_email
        mapping.advisor_phone = advisor_phone
        mapping.advisor_id = advisor_id
        mapping.institution_id = user.institution_id
        mapping.institution_name = user.institution_name
        mapping.institution_type = user.institution_type

    try:
        db.session.commit()
        
        if new_advisor_created:
            try:
                welcome_msg = (
                    f"Welcome to NexAttend AI! \n\n"
                    f"You have been registered as a Class Advisor at {user.institution_name}.\n\n"
                    f"📧 Login Email: {advisor_email}\n"
                    f"🔑 Temporary Password: {password}\n"
                    f"🔗 Login URL: {request.url_root.rstrip('/')}\n\n"
                    f"Please log in and complete your Face Registration to activate your account."
                )
                NotificationService.send_whatsapp(
                    to_number=advisor_phone,
                    message=welcome_msg,
                    recipient_name=advisor_name,
                    msg_type="Advisor Welcome Notification"
                )
            except Exception as e:
                print(f"[WhatsApp] Failed to send onboarding welcome: {e}")
                
        advisor_face_registered = False
        if advisor_user:
            advisor_face_registered = bool(advisor_user.face_registered)
        return jsonify({
            'success': True,
            'message': 'Class advisor mapping saved successfully.',
            'advisor_face_registered': advisor_face_registered,
            'advisor_id': advisor_user.id if advisor_user else None,
            'class_section_id': mapping.id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@app.route('/api/settings/delete_advisor_map', methods=['POST'])
def api_delete_advisor_map():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    user = get_current_user()
    if not user or user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Access Denied: Only Teachers can manage advisor mappings.'}), 403
        
    data = request.get_json() or {}
    mapping_id = data.get('id')
    if not mapping_id:
        return jsonify({'success': False, 'message': 'Mapping ID is required.'}), 400
        
    from models import ClassSection
    mapping = ClassSection.query.get(mapping_id)
    if not mapping:
        return jsonify({'success': False, 'message': 'Mapping record not found.'}), 404
        
    # IDOR scope check
    if mapping.institution_id != user.institution_id:
        return jsonify({'success': False, 'message': 'Access Denied: Institution mismatch.'}), 403
        
    try:
        db.session.delete(mapping)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Class advisor mapping deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

@app.route('/api/attendance/submit_review', methods=['POST'])
def api_submit_review():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401
        
    user = get_current_user()
    if not user or user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Access Denied.'}), 403
        
    mapping = resolve_current_class_section(user)
    if not mapping or not mapping.advisor_id:
        return jsonify({
            'success': False,
            'message': f"Warning: No Class Advisor is currently assigned to this class ({dept} {year} - {section}). Review submission aborted."
        }), 400
        
    dept = user.department_or_class
    year = user.year or ""
    section = user.section
    
    today = date.today()
    sess_rec = get_or_create_daily_session(dept, year, section, today, mapping.id)
    
    success, message = submit_attendance_session_to_advisor(sess_rec.id, user_id=user.id, is_automatic=False)
    if not success:
        return jsonify({'success': False, 'message': message}), 400
        
    return jsonify({'success': True, 'message': message})

@app.route('/api/attendance/approve_finalize', methods=['POST'])
def api_approve_finalize():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized. Please authenticate.'}), 401
        
    user = get_current_user()
    if not user or user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Access Denied.'}), 403
        
    data = request.get_json() or {}
    dept = data.get('department', '').strip() or user.department_or_class
    year = data.get('year', '').strip() or (user.year or "")
    section = data.get('section', '').strip() or user.section
    
    from models import ClassSection, AttendanceSession, User, Attendance, NotificationLog
    
    today = date.today()
    sess_rec = AttendanceSession.query.filter_by(
        department=dept,
        year=year,
        section=section,
        attendance_date=today
    ).first()
    
    if not sess_rec:
        return jsonify({'success': False, 'message': 'No attendance session found for today.'}), 400
        
    # Resolve mapping robustly
    mapping = None
    if sess_rec.class_section_id:
        mapping = ClassSection.query.get(sess_rec.class_section_id)
    if not mapping:
        mapping = ClassSection.query.filter_by(advisor_id=user.id).first()
    if not mapping and user.email:
        mapping = ClassSection.query.filter_by(advisor_email=user.email).first()
        
    # IDOR Check: only the mapped Class Advisor can approve/finalize
    if mapping and mapping.advisor_id != user.id:
        return jsonify({'success': False, 'message': 'Access Denied: Only the mapped Class Advisor can finalize attendance.'}), 403
            
    if sess_rec.status == 'Draft':
        return jsonify({'success': False, 'message': 'Cannot finalize draft session. Please submit for review first.'}), 400
        
    if sess_rec.status == 'Finalized':
        return jsonify({'success': False, 'message': 'Attendance already finalized for today.'}), 400
        
    # Update status
    sess_rec.status = 'Finalized'
    sess_rec.is_finalized = True
    sess_rec.finalized_at = datetime.utcnow()
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Database error: {e}'}), 500
        
    # Trigger Parent WhatsApp alerts for absent students
    if mapping:
        students = User.query.filter(
            (User.role == 'student') &
            (
                (User.class_section_id == mapping.id) |
                (
                    (User.department_or_class == dept) &
                    (User.year == (year or "")) &
                    (User.section == section)
                )
            )
        ).all()
    else:
        students = User.query.filter(
            (User.role == 'student') &
            (User.department_or_class == dept) &
            (User.year == (year or "")) &
            (User.section == section)
        ).all()
    
    parent_logs = []
    absent_count = 0
    for s in students:
        att = Attendance.query.filter_by(user_id=s.id, date=today).first()
        if att and att.status == 'Absent':
            absent_count += 1
            if s.parent_phone:
                msg = (
                    f"NexAttend AI Alert: \n\n"
                    f"Dear Parent, your ward {s.name} was marked ABSENT today ({today.strftime('%Y-%m-%d')}) "
                    f"for class {dept} {year or ''} Section {section}."
                )
                notif = NotificationLog(
                    type="Parent Absent Alert",
                    recipient_contact=s.parent_phone,
                    message=msg,
                    status="Pending",
                    timestamp=datetime.utcnow()
                )
                db.session.add(notif)
                db.session.commit()
                parent_logs.append(notif)
                
                # Send async WhatsApp
                import threading
                from flask import current_app
                def send_parent_whatsapp(app_ctx, log_id, to_no, msg_body, name):
                    with app_ctx:
                        try:
                            NotificationService.send_whatsapp(
                                to_number=to_no,
                                message=msg_body,
                                recipient_name=name,
                                msg_type="Parent Absent Alert"
                            )
                            l_rec = NotificationLog.query.get(log_id)
                            if l_rec:
                                l_rec.status = "Sent"
                                db.session.commit()
                        except Exception as ex:
                            l_rec = NotificationLog.query.get(log_id)
                            if l_rec:
                                l_rec.status = "Failed"
                                l_rec.error_details = str(ex)
                                db.session.commit()
                                
                threading.Thread(
                    target=send_parent_whatsapp,
                    args=(current_app._get_current_object().app_context(), notif.id, s.parent_phone, msg, s.name),
                    daemon=True
                ).start()
            else:
                notif = NotificationLog(
                    type="Parent Absent Alert",
                    recipient_contact="",
                    message=f"Student {s.name} marked Absent.",
                    status="Failed",
                    error_details="Parent phone number not registered",
                    timestamp=datetime.utcnow()
                )
                db.session.add(notif)
                db.session.commit()
                parent_logs.append(notif)
                
    return jsonify({
        'success': True,
        'message': f"Attendance approved & finalized. Parent WhatsApp alerts triggered for {len(parent_logs)} absentees.",
        'stats': {
            'total': len(students),
            'present': len(students) - absent_count,
            'absent': absent_count
        }
    })

@app.route('/api/settings/time_settings', methods=['GET', 'POST'])
def api_time_settings():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 401
        
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
    from models import ClassSection
    
    if request.method == 'GET':
        classes = resolve_authorized_class_sections(user)
        classes_data = []
        for c in classes:
            classes_data.append({
                'id': c.id,
                'department': c.department,
                'year': c.year,
                'section': c.section,
                'start_time': c.start_time.strftime('%H:%M') if c.start_time else '09:00',
                'end_time': c.end_time.strftime('%H:%M') if c.end_time else '09:30',
                'auto_submit_enabled': bool(c.auto_submit_enabled)
            })
        return jsonify({'success': True, 'classes': classes_data})
        
    elif request.method == 'POST':
        data = request.get_json() or {}
        class_id = data.get('class_id')
        start_time_str = data.get('start_time', '').strip()
        end_time_str = data.get('end_time', '').strip()
        auto_submit_enabled = data.get('auto_submit_enabled')
        
        if not class_id or not start_time_str or not end_time_str:
            return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
            
        c_sec = ClassSection.query.get(class_id)
        if not c_sec:
            return jsonify({'success': False, 'message': 'Class section not found.'}), 404
            
        if not verify_class_access(user, c_sec):
            return jsonify({'success': False, 'message': 'Access Denied.'}), 403
            
        try:
            start_time = datetime.strptime(start_time_str, '%H:%M').time()
            end_time = datetime.strptime(end_time_str, '%H:%M').time()
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid time format. Use HH:MM.'}), 400
            
        c_sec.start_time = start_time
        c_sec.end_time = end_time
        c_sec.auto_submit_enabled = bool(auto_submit_enabled)
        
        try:
            db.session.commit()
            return jsonify({'success': True, 'message': 'Timing settings updated successfully.'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Database error: {e}'}), 500

def api_analytics_data():
    """Aggregates analytics stats for Chart.js — scoped to the logged-in user's class section."""
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    if not user:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    # Resolve the class section(s) this user is authorized to view
    cs = resolve_active_class_section(user)
    if cs:
        # Scope to students in this class section only
        authorized_students = get_students_for_class(user, cs)
    else:
        authorized_students = []

    student_ids = [s.id for s in authorized_students]

    # 1. Overall attendance rates (Present vs Absent) — scoped
    if student_ids:
        total_attendance = Attendance.query.filter(Attendance.user_id.in_(student_ids)).count()
        present_count = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.status == 'Present').count()
        absent_count = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.status == 'Absent').count()
    else:
        total_attendance = 0
        present_count = 0
        absent_count = 0

    # 2. Department-wise attendance percentage — scoped to authorized students
    dept_groups = {}
    for s in authorized_students:
        dept = s.department_or_class or 'Unknown'
        dept_groups.setdefault(dept, []).append(s.id)

    dept_labels = list(dept_groups.keys())
    dept_present_rates = []
    for dept, ids in dept_groups.items():
        total_dept_att = Attendance.query.filter(Attendance.user_id.in_(ids)).count()
        present_dept_att = Attendance.query.filter(Attendance.user_id.in_(ids), Attendance.status == 'Present').count()
        rate = (present_dept_att / total_dept_att * 100) if total_dept_att > 0 else 100.0
        dept_present_rates.append(round(rate, 1))

    # 3. Weekly trend (last 7 days of activity for authorized students)
    if student_ids:
        dates_query = db.session.query(Attendance.date).filter(
            Attendance.user_id.in_(student_ids)
        ).distinct().order_by(Attendance.date.desc()).limit(7).all()
    else:
        dates_query = []
    active_dates = sorted([d[0] for d in dates_query])

    trend_labels = [d.strftime('%b %d') for d in active_dates]
    trend_present_pct = []
    for d in active_dates:
        if student_ids:
            tot = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == d).count()
            pres = Attendance.query.filter(Attendance.user_id.in_(student_ids), Attendance.date == d, Attendance.status == 'Present').count()
        else:
            tot = 0
            pres = 0
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


def run_attendance_scheduler(app_instance):
    import time
    from datetime import datetime, date
    from models import db, ClassSection, AttendanceSession
    
    print("[SCHEDULER] Background scheduler started.", flush=True)
    while True:
        time.sleep(30)
        try:
            with app_instance.app_context():
                now = datetime.now()
                current_date = now.date()
                current_time = now.time()
                
                classes = ClassSection.query.all()
                for c_sec in classes:
                    if c_sec.auto_submit_enabled and c_sec.end_time and c_sec.advisor:
                        dept = c_sec.advisor.department_or_class
                        yr = c_sec.advisor.year or ""
                        sec = c_sec.advisor.section
                        if current_time >= c_sec.end_time:
                            sess_rec = AttendanceSession.query.filter_by(
                                department=dept,
                                year=yr,
                                section=sec,
                                attendance_date=current_date
                            ).first()
                            
                            if not sess_rec:
                                sess_rec = get_or_create_daily_session(dept, yr, sec, current_date, c_sec.id)
                                
                            if sess_rec.status == 'Draft':
                                success, res = submit_attendance_session_to_advisor(sess_rec.id, is_automatic=True)
                                if success:
                                    print(f"[SCHEDULER] Auto-submitted session {sess_rec.id} successfully.", flush=True)
                                else:
                                    print(f"[SCHEDULER] Auto-submission failed for session {sess_rec.id}: {res}", flush=True)
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}", flush=True)


# ----------------- DB SETUP & RUN -----------------

if __name__ == '__main__':
    with app.app_context():
        from sqlalchemy import text
        # Drop old class_sections table if it contains the department column
        try:
            db.session.execute(text("SELECT department FROM class_sections LIMIT 1"))
            db.session.execute(text("DROP TABLE class_sections"))
            db.session.commit()
            print("[MIGRATION] Dropped old class_sections table successfully.", flush=True)
        except Exception:
            db.session.rollback()
        # 1. start_time on class_sections
        try:
            db.session.execute(text("SELECT start_time FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN start_time TIME NOT NULL DEFAULT '09:00:00'"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 2. end_time on class_sections
        try:
            db.session.execute(text("SELECT end_time FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN end_time TIME NOT NULL DEFAULT '09:30:00'"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 3. auto_submit_enabled on class_sections
        try:
            db.session.execute(text("SELECT auto_submit_enabled FROM class_sections LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE class_sections ADD COLUMN auto_submit_enabled BOOLEAN NOT NULL DEFAULT 0"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 4. is_automatic on attendance_sessions
        try:
            db.session.execute(text("SELECT is_automatic FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN is_automatic BOOLEAN NOT NULL DEFAULT 0"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 5. class_section_id on attendance_sessions
        try:
            db.session.execute(text("SELECT class_section_id FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN class_section_id INTEGER REFERENCES class_sections(id) ON DELETE SET NULL"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 6. submitted_at on attendance_sessions
        try:
            db.session.execute(text("SELECT submitted_at FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN submitted_at DATETIME"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 7. submitted_by on attendance_sessions
        try:
            db.session.execute(text("SELECT submitted_by FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN submitted_by INTEGER REFERENCES users(id) ON DELETE SET NULL"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 8. finalized_at on attendance_sessions
        try:
            db.session.execute(text("SELECT finalized_at FROM attendance_sessions LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE attendance_sessions ADD COLUMN finalized_at DATETIME"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        # 9. creator_id on users
        try:
            db.session.execute(text("SELECT creator_id FROM users LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text("ALTER TABLE users ADD COLUMN creator_id INTEGER REFERENCES users(id) ON DELETE SET NULL"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                
        db.create_all()
        
    import threading
    t = threading.Thread(target=run_attendance_scheduler, args=(app,), daemon=True)
    t.start()
    
    app.run(host='0.0.0.0', port=5000, debug=True)
