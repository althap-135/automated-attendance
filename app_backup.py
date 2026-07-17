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

# ----------------- ROUTE PROTECTION MIDDLEWARE -----------------
@app.before_request
def require_login():
    # Endpoints allowed without authentication
    allowed_routes = ['login', 'register', 'api_login', 'api_register', 'static']
    # Specific API paths allowed without session
    allowed_apis = ['/api/attendance/scan', '/api/settings/seed']
    
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
    role = data.get('role', '').strip() # "student", "teacher", "admin"
    
    if not name or not email or not password or not institution_type or not institution_name or not dept_or_class or not section or not role:
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
        
    # Set session keys
    session['logged_in'] = True
    session['user_id'] = user.id
    session['user_role'] = user.role
    
    # Compatibility keys
    session['teacher_name'] = user.name
    session['teacher_id'] = user.email
    
    return jsonify({'success': True, 'message': f'Welcome back, {user.name}!'})


# ----------------- PAGE ROUTES -----------------

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
    if not user or user.role not in ['teacher', 'admin']:
        return "Access denied. Only teachers or admins can access this page.", 403
        
    if user.role == 'teacher' and not session.get('teacher_face_verified'):
        return redirect(url_for('teacher_auth_page'))
        
    today = date.today()
    students = User.query.filter_by(
        role='student',
        institution_name=user.institution_name,
        department_or_class=user.department_or_class,
        section=user.section
    ).all()
    
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
        
    return render_template('correction.html', students=student_list, today_str=today.strftime('%Y-%m-%d'))

@app.route('/search')
def search_page():
    return render_template('search.html')

@app.route('/student/<register_number>')
def student_details(register_number):
    student = User.query.filter_by(role='student', email=register_number).first_or_404()
    
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
    # Fetch overall attendance date-wise summary
    db_records = Attendance.query.order_by(Attendance.date.desc(), Attendance.time.desc()).all()
    audit_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).all()
    
    return render_template('history.html', records=db_records, audit_logs=audit_logs)

@app.route('/analytics')
def analytics_page():
    return render_template('analytics.html')

@app.route('/settings')
def settings_page():
    students = User.query.filter_by(role='student').all()
    teachers = User.query.filter_by(role='teacher').all()
    notification_logs = NotificationLog.query.order_by(NotificationLog.timestamp.desc()).all()
    
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
        notification_logs=notification_logs,
        ai_provider=ai_config.get('provider', 'openai'),
        ai_key=masked_key
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
        teacher = User.query.filter_by(id=matched_id, role='teacher').first()
        if not teacher:
            return jsonify({'success': False, 'message': 'Teacher record not found in database.'})
            
        # Log teacher session
        session['logged_in'] = True
        session['user_id'] = teacher.id
        session['user_role'] = teacher.role
        session['teacher_id'] = teacher.email
        session['teacher_name'] = teacher.name
        session['teacher_pk'] = teacher.id
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

@app.route('/api/student/search', methods=['GET'])
def api_search_students():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
        
    # Search by register number or name
    results = User.query.filter(User.role == 'student').filter(
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
    
    system_prompt = f"""You are the official AI Assistant for the Smart AI-Based Face Recognition Attendance and Student Management System.

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
                "Hi! I am the SmartFace Local AI Helper. I couldn't reach the online LLM, but I can still answer basic questions about attendance stats and records!\n\n"
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
    data = request.get_json()
    if not data or 'role' not in data or 'name' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    role = data['role']
    name = data['name'].strip()
    
    if not name:
        return jsonify({'success': False, 'message': 'Name cannot be empty.'}), 400
        
    if role == 'student':
        reg_num = data.get('register_number', '').strip() # email
        dept = data.get('department', '').strip()
        year = data.get('year', '').strip()
        section = data.get('section', 'A').strip()
        mobile_number = data.get('mobile_number', '').strip()
        parent_mobile_number = data.get('parent_mobile_number', '').strip()
        
        if not reg_num or not dept or not year or not parent_mobile_number:
            return jsonify({'success': False, 'message': 'Missing required student fields (Register Number, Dept, Year, and Parent Mobile Number are required).'}), 400
            
        # Check duplicate user
        existing = User.query.filter_by(email=reg_num).first()
        if existing:
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
            
        new_student = User(
            name=name,
            email=reg_num,
            password=generate_password_hash("password123"), # default password
            institution_type="College",
            institution_name="VSB Engineering College",
            department_or_class=dept,
            year=year,
            section=section,
            role=role,
            mobile_number=mobile_number or None,
            parent_mobile_number=parent_mobile_number
        )
        db.session.add(new_student)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Student {name} registered successfully.'})
        
    elif role == 'teacher':
        teacher_id = data.get('teacher_id', '').strip() # email
        email = data.get('email', '').strip() # subject or subject name (preserve params)
        
        if not teacher_id or not email:
            return jsonify({'success': False, 'message': 'Missing required teacher fields.'}), 400
            
        # Check duplicate teacher ID
        existing = User.query.filter_by(email=teacher_id).first()
        if existing:
            return jsonify({'success': False, 'message': 'This email ID is already registered'}), 400
            
        new_teacher = User(
            name=name,
            email=teacher_id,
            password=generate_password_hash("password123"),
            institution_type="College",
            institution_name="VSB Engineering College",
            department_or_class="Computer Science",
            year="3rd Year",
            section="A",
            role=role
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
        
    try:
        # Seed Students
        students_data = [
            User(name="Alice Vance", email="alice@college.edu", password=generate_password_hash("password123"), institution_type="College", institution_name="VSB Engineering College", department_or_class="Computer Science", year="3rd Year", section="A", role="student", mobile_number="+919876543210", parent_mobile_number="+919876543211"),
            User(name="Bob Miller", email="bob@college.edu", password=generate_password_hash("password123"), institution_type="College", institution_name="VSB Engineering College", department_or_class="Computer Science", year="3rd Year", section="A", role="student", mobile_number="+919876543220", parent_mobile_number="+919876543221"),
            User(name="Charlie Song", email="charlie@college.edu", password=generate_password_hash("password123"), institution_type="College", institution_name="VSB Engineering College", department_or_class="Electronics", year="2nd Year", section="B", role="student", mobile_number="+919876543230", parent_mobile_number="+919876543231"),
            User(name="Diana Prince", email="diana@college.edu", password=generate_password_hash("password123"), institution_type="College", institution_name="VSB Engineering College", department_or_class="Mechanical", year="4th Year", section="A", role="student", mobile_number="+919876543240", parent_mobile_number="+919876543241"),
            User(name="Ethan Hunt", email="ethan@college.edu", password=generate_password_hash("password123"), institution_type="College", institution_name="VSB Engineering College", department_or_class="Information Technology", year="1st Year", section="A", role="student", mobile_number="+919876543250", parent_mobile_number="+919876543251")
        ]
        
        # Seed Teachers
        teachers_data = [
            User(name="Dr. Sarah Connor", email="sarah.connor@college.edu", password=generate_password_hash("password123"), institution_type="College", institution_name="VSB Engineering College", department_or_class="Computer Science", year="3rd Year", section="A", role="teacher"),
            User(name="Prof. Charles Xavier", email="charles.xavier@college.edu", password=generate_password_hash("password123"), institution_type="College", institution_name="VSB Engineering College", department_or_class="Computer Science", year="3rd Year", section="A", role="teacher")
        ]
        
        db.session.add_all(students_data)
        db.session.add_all(teachers_data)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Successfully seeded 5 students and 2 teachers.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f"Error seeding database: {e}"}), 500

@app.route('/api/settings/enroll', methods=['POST'])
def api_enroll_face():
    """
    Saves snapshot (1 to 5) for a user face.
    If it's the 5th snapshot, trains the model and marks user as registered.
    """
    data = request.get_json()
    if not data or 'image' not in data or 'user_type' not in data or 'user_id' not in data or 'snapshot_index' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    user_type = data['user_type'] # 'student' or 'teacher'
    user_id = int(data['user_id'])
    snapshot_index = int(data['snapshot_index']) # 1 to 5
    
    img_bgr = decode_base64_image(data['image'])
    if img_bgr is None:
        return jsonify({'success': False, 'message': 'Failed to decode image.'}), 400
        
    success, message = FaceService.save_face_snapshots(img_bgr, user_type, user_id, snapshot_index)
    
    if not success:
        return jsonify({'success': False, 'message': message})
        
    # If 5th snapshot, train the model
    if snapshot_index == 5:
        # Mark as face registered
        user = User.query.get(user_id)
            
        if user:
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
    data = request.get_json()
    if not data or 'user_type' not in data or 'user_id' not in data:
        return jsonify({'success': False, 'message': 'Missing parameters.'}), 400
        
    user_type = data['user_type']
    user_id = int(data['user_id'])
    
    user = User.query.get(user_id)
    if user_type == 'student':
        parent_dir = app.config['STUDENT_FACES_DIR']
    else:
        parent_dir = app.config['TEACHER_FACES_DIR']
        
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
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


# ----------------- ANALYTICS API -----------------

@app.route('/api/analytics/data')
def api_analytics_data():
    """Aggregates analytics stats for Chart.js"""
    # 1. Overall attendance rates (Present vs Absent)
    total_attendance = Attendance.query.count()
    present_count = Attendance.query.filter_by(status='Present').count()
    absent_count = Attendance.query.filter_by(status='Absent').count()
    
    # 2. Department-wise attendance percentage
    # Query all students grouped by department
    departments = db.session.query(User.department_or_class).filter(User.role == 'student').distinct().all()
    dept_labels = [d[0] for d in departments if d[0]]
    dept_present_rates = []
    
    for dept in dept_labels:
        # Students in department
        dept_student_ids = [s.id for s in User.query.filter_by(role='student', department_or_class=dept).all()]
        if not dept_student_ids:
            dept_present_rates.append(100.0)
            continue
            
        total_dept_att = Attendance.query.filter(Attendance.user_id.in_(dept_student_ids)).count()
        present_dept_att = Attendance.query.filter(Attendance.user_id.in_(dept_student_ids), Attendance.status == 'Present').count()
        
        rate = (present_dept_att / total_dept_att * 100) if total_dept_att > 0 else 100.0
        dept_present_rates.append(round(rate, 1))
        
    # 3. Weekly trend (last 7 days of school activity)
    # Get last 7 active dates in attendance
    dates_query = db.session.query(Attendance.date).distinct().order_by(Attendance.date.desc()).limit(7).all()
    active_dates = sorted([d[0] for d in dates_query])
    
    trend_labels = [d.strftime('%b %d') for d in active_dates]
    trend_present_pct = []
    
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
