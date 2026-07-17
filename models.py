from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()

class Institution(db.Model):
    __tablename__ = 'institutions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    institution_type = db.Column(db.String(50), nullable=False) # "College" or "School"

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    institution_type = db.Column(db.String(50), nullable=True) # "College" or "School"
    institution_name = db.Column(db.String(150), nullable=True)
    department_or_class = db.Column(db.String(100), nullable=False)
    year = db.Column(db.String(50), nullable=True) # e.g. "II Year" for College, null for School
    section = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(50), nullable=False) # "student", "teacher"
    face_registered = db.Column(db.Boolean, default=False)
    mobile_number = db.Column(db.String(50), nullable=True)
    parent_mobile_number = db.Column(db.String(50), nullable=True)
    reg_number = db.Column(db.String(100), unique=True, nullable=True, index=True)
    parent_phone = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    institution_id = db.Column(db.Integer, db.ForeignKey('institutions.id', ondelete='SET NULL'), nullable=True)
    class_section_id = db.Column(db.Integer, db.ForeignKey('class_sections.id', ondelete='SET NULL'), nullable=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    
    # Relationships
    institution = db.relationship('Institution', backref='users')
    attendances = db.relationship('Attendance', backref='user', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'institution_type': self.institution_type or '',
            'institution_name': self.institution_name or '',
            'institution_id': self.institution_id,
            'class_section_id': self.class_section_id,
            'creator_id': self.creator_id,
            'department_or_class': self.department_or_class,
            'year': self.year or '',
            'section': self.section,
            'role': self.role,
            'face_registered': self.face_registered,
            'mobile_number': self.mobile_number or '',
            'parent_mobile_number': self.parent_mobile_number or '',
            'reg_number': self.reg_number or '',
            'parent_phone': self.parent_phone or ''
        }

class ClassSection(db.Model):
    __tablename__ = 'class_sections'
    
    id = db.Column(db.Integer, primary_key=True)
    advisor_name = db.Column(db.String(100), nullable=False, default="")
    advisor_email = db.Column(db.String(100), nullable=False, default="")
    advisor_phone = db.Column(db.String(50), nullable=False, default="")
    advisor_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    
    institution_id = db.Column(db.Integer, db.ForeignKey('institutions.id', ondelete='CASCADE'), nullable=True)
    institution_name = db.Column(db.String(150), nullable=True)
    institution_type = db.Column(db.String(50), nullable=True)
    
    # Time settings columns (TIME datatype)
    start_time = db.Column(db.Time, nullable=False, default=lambda: datetime.strptime("09:00", "%H:%M").time())
    end_time = db.Column(db.Time, nullable=False, default=lambda: datetime.strptime("09:30", "%H:%M").time())
    auto_submit_enabled = db.Column(db.Boolean, nullable=False, default=True)
    
    advisor = db.relationship('User', foreign_keys=[advisor_id], backref='advised_classes')
    institution = db.relationship('Institution', backref='class_sections')
    
    __table_args__ = (
        db.UniqueConstraint('institution_id', 'advisor_email', name='uq_institution_advisor'),
    )
    
    def __init__(self, **kwargs):
        # Pop out legacy class fields if passed, to ensure backward compatibility and prevent TypeErrors in tests
        self._dept = kwargs.pop('department', "")
        self._yr = kwargs.pop('year', "")
        self._sec = kwargs.pop('section', "")
        super().__init__(**kwargs)
        
    @property
    def department(self):
        val = getattr(self, '_dept', "")
        if not val and self.advisor:
            return self.advisor.department_or_class
        return val
    @department.setter
    def department(self, value):
        self._dept = value
        
    @property
    def year(self):
        val = getattr(self, '_yr', "")
        if not val and self.advisor:
            return self.advisor.year
        return val
    @year.setter
    def year(self, value):
        self._yr = value
        
    @property
    def section(self):
        val = getattr(self, '_sec', "")
        if not val and self.advisor:
            return self.advisor.section
        return val
    @section.setter
    def section(self, value):
        self._sec = value
    
    def to_dict(self):
        return {
            'id': self.id,
            'advisor_id': self.advisor_id,
            'advisor_name': self.advisor_name,
            'advisor_email': self.advisor_email,
            'advisor_phone': self.advisor_phone,
            'institution_id': self.institution_id,
            'institution_name': self.institution_name or '',
            'institution_type': self.institution_type or '',
            'start_time': self.start_time.strftime('%H:%M') if self.start_time else '09:00',
            'end_time': self.end_time.strftime('%H:%M') if self.end_time else '09:30',
            'auto_submit_enabled': self.auto_submit_enabled
        }

class AttendanceSession(db.Model):
    __tablename__ = 'attendance_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    department = db.Column(db.String(100), nullable=False)
    year = db.Column(db.String(50), nullable=False)
    section = db.Column(db.String(50), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    is_finalized = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(50), default="Draft", nullable=False) # "Draft", "Submitted", "Finalized"
    submitted_at = db.Column(db.DateTime, nullable=True)
    submitted_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    finalized_at = db.Column(db.DateTime, nullable=True)
    finalized_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    report_status = db.Column(db.String(50), default="Pending", nullable=False)
    is_automatic = db.Column(db.Boolean, default=False, nullable=False)
    
    class_section_id = db.Column(db.Integer, db.ForeignKey('class_sections.id', ondelete='SET NULL'), nullable=True)
    class_section = db.relationship('ClassSection', backref='attendance_sessions')
    
    __table_args__ = (db.UniqueConstraint('department', 'year', 'section', 'attendance_date', name='uq_class_date'),)

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

    @property
    def student_name(self):
        return self.user.name if self.user else "Unknown"

    @property
    def register_number(self):
        if self.user and self.user.reg_number:
            return self.user.reg_number
        return "N/A"

    @property
    def department(self):
        if self.user:
            from models import ClassSection
            if self.user.class_section_id:
                cs = ClassSection.query.filter_by(id=self.user.class_section_id).first()
                if cs:
                    return cs.department
            return self.user.department_or_class or "N/A"
        return "N/A"

    @property
    def year(self):
        if self.user:
            from models import ClassSection
            if self.user.class_section_id:
                cs = ClassSection.query.filter_by(id=self.user.class_section_id).first()
                if cs:
                    return cs.year
            return self.user.year or "N/A"
        return "N/A"

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'student_name': self.student_name,
            'register_number': self.register_number,
            'department': self.department,
            'year': self.year,
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
    class_id = db.Column(db.String(100), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    advisor_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    recipient_name = db.Column(db.String(100), nullable=True)
    recipient_contact = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False) # "Daily Attendance Report", "Parent Absent Alert"
    channel = db.Column(db.String(50), default="WhatsApp", nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="Pending", nullable=False)
    provider_message_id = db.Column(db.String(100), nullable=True)
    error_details = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    class_section_id = db.Column(db.Integer, db.ForeignKey('class_sections.id', ondelete='SET NULL'), nullable=True)
    
    student = db.relationship('User', foreign_keys=[student_id])
    advisor = db.relationship('User', foreign_keys=[advisor_id])
    class_section = db.relationship('ClassSection', backref='notification_logs')

    def to_dict(self):
        return {
            'id': self.id,
            'class_id': self.class_id or '',
            'class_section_id': self.class_section_id,
            'student_id': self.student_id or '',
            'advisor_id': self.advisor_id or '',
            'recipient_name': self.recipient_name or '',
            'recipient_contact': self.recipient_contact,
            'type': self.type,
            'channel': self.channel,
            'message': self.message,
            'status': self.status,
            'provider_message_id': self.provider_message_id or '',
            'error_details': self.error_details or '',
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }



