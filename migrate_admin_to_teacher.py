"""migrate_admin_to_teacher.py - run from project root"""
import sys
sys.path.insert(0, '.')

from app import app, db
from models import User

with app.app_context():
    admins = User.query.filter_by(role='admin').all()
    print(f"Found {len(admins)} admin user(s):")
    for u in admins:
        print(f"  - ID={u.id}  Name={u.name!r}  Email={u.email!r}")
        u.role = 'teacher'
    if admins:
        db.session.commit()
        print("Migration complete.")
    remaining = User.query.filter_by(role='admin').count()
    print(f"Remaining admin users: {remaining}")
