from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    institution_type = db.Column(db.String(50), nullable=False) # "College" or "School"
    institution_name = db.Column(db.String(150), nullable=False)
    department_or_class = db.Column(db.String(100), nullable=False)
    year = db.Column(db.String(50), nullable=True) # e.g. "II Year" for College, null for School
    section = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(50), nullable=False) # "student", "teacher", "admin"
    face_registered = db.Column(db.Boolean, default=False)
    mobile_number = db.Column(db.String(50), nullable=True)
    parent_mobile_number = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to attendance records
    attendances = db.relationship('Attendance', backref='user', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'institution_type': self.institution_type,
            'institution_name': self.institution_name,
            'department_or_class': self.department_or_class,
            'year': self.year or '',
            'section': self.section,
            'role': self.role,
            'face_registered': self.face_registered,
            'mobile_number': self.mobile_number or '',
            'parent_mobile_number': self.parent_mobile_number or ''
        }

class Attendance(db.Model):
    __tablename__ = 'attendance'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    time = db.Column(db.Time, nullable=False, default=lambda: datetime.now().time())
    status = db.Column(db.String(20), nullable=False, default="Absent") # "Present" or "Absent"
    marked_by = db.Column(db.String(100), nullable=False, default="Face Recognition") # "Face Recognition", "Teacher Name (ID)"
    
    # Unique constraint per user per day to prevent duplicates
    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='uq_user_date'),)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'student_name': self.user.name if self.user else "Unknown",
            'register_number': self.user.email if self.user else "N/A",
            'department': self.user.department_or_class if self.user else "N/A",
            'year': self.user.year if self.user else "N/A",
            'date': self.date.strftime('%Y-%m-%d'),
            'time': self.time.strftime('%H:%M:%S') if self.time else "N/A",
            'status': self.status,
            'marked_by': self.marked_by
        }

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    student_name = db.Column(db.String(100), nullable=False)
    register_number = db.Column(db.String(50), nullable=False)
    teacher_name = db.Column(db.String(100), nullable=False)
    teacher_id = db.Column(db.String(50), nullable=False)
    previous_status = db.Column(db.String(20), nullable=False)
    updated_status = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'student_name': self.student_name,
            'register_number': self.register_number,
            'teacher_name': self.teacher_name,
            'teacher_id': self.teacher_id,
            'previous_status': self.previous_status,
            'updated_status': self.updated_status,
            'reason': self.reason,
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }

class NotificationLog(db.Model):
    __tablename__ = 'notification_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    recipient_name = db.Column(db.String(100), nullable=False)
    recipient_contact = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False) # "Parent Absent Alert", "Teacher Daily Summary"
    channel = db.Column(db.String(50), nullable=False) # "WhatsApp", "Email", "SMS"
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="Sent", nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'recipient_name': self.recipient_name,
            'recipient_contact': self.recipient_contact,
            'type': self.type,
            'channel': self.channel,
            'message': self.message,
            'status': self.status,
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }



