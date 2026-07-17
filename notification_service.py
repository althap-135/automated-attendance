import smtplib
from email.mime.text import MIMEText
import requests
from flask import current_app
from models import db, NotificationLog

class NotificationService:
    @staticmethod
    def log_notification(recipient_name, recipient_contact, msg_type, channel, message, status):
        """Helper to log the notification to the database."""
        try:
            log = NotificationLog(
                recipient_name=recipient_name,
                recipient_contact=recipient_contact,
                type=msg_type,
                channel=channel,
                message=message,
                status=status
            )
            db.session.add(log)
            db.session.commit()
            return log
        except Exception as e:
            db.session.rollback()
            print(f"Error logging notification: {e}")
            return None

    @staticmethod
    def send_whatsapp(to_number, message, recipient_name, msg_type):
        """
        Sends a WhatsApp message using Twilio API (if credentials are set)
        otherwise simulates the sending and logs it.
        """
        account_sid = current_app.config['TWILIO_ACCOUNT_SID']
        auth_token = current_app.config['TWILIO_AUTH_TOKEN']
        from_number = current_app.config['TWILIO_WHATSAPP_NUMBER']
        
        # Ensure to_number is in WhatsApp format if using Twilio
        to_whatsapp = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
        
        if not account_sid or not auth_token:
            # Simulation Mode
            NotificationService.log_notification(
                recipient_name=recipient_name,
                recipient_contact=to_number,
                msg_type=msg_type,
                channel="WhatsApp",
                message=message,
                status="Simulated"
            )
            return True, "Simulation: WhatsApp logged."

        # Real Twilio API Call
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            data = {
                "From": from_number,
                "To": to_whatsapp,
                "Body": message
            }
            response = requests.post(url, data=data, auth=(account_sid, auth_token))
            
            if response.status_code in [200, 201]:
                NotificationService.log_notification(
                    recipient_name=recipient_name,
                    recipient_contact=to_number,
                    msg_type=msg_type,
                    channel="WhatsApp",
                    message=message,
                    status="Sent"
                )
                return True, "WhatsApp sent successfully."
            else:
                error_msg = f"Twilio API Error: {response.text}"
                NotificationService.log_notification(
                    recipient_name=recipient_name,
                    recipient_contact=to_number,
                    msg_type=msg_type,
                    channel="WhatsApp",
                    message=message,
                    status=f"Failed ({response.status_code})"
                )
                return False, error_msg
        except Exception as e:
            NotificationService.log_notification(
                recipient_name=recipient_name,
                recipient_contact=to_number,
                msg_type=msg_type,
                channel="WhatsApp",
                message=message,
                status=f"Error: {str(e)[:15]}"
            )
            return False, str(e)

    @staticmethod
    def send_email(to_email, subject, body_text, recipient_name, msg_type):
        """
        Sends an email using standard SMTP (if configured)
        otherwise simulates the sending and logs it.
        """
        smtp_server = current_app.config['SMTP_SERVER']
        smtp_port = current_app.config['SMTP_PORT']
        from_email = current_app.config['SMTP_EMAIL']
        smtp_password = current_app.config['SMTP_PASSWORD']
        
        full_msg_content = f"Subject: {subject}\n\n{body_text}"
        
        if not from_email or not smtp_password:
            # Simulation Mode
            NotificationService.log_notification(
                recipient_name=recipient_name,
                recipient_contact=to_email,
                msg_type=msg_type,
                channel="Email",
                message=full_msg_content,
                status="Simulated"
            )
            return True, "Simulation: Email logged."

        # Real SMTP Delivery
        try:
            msg = MIMEText(body_text)
            msg['Subject'] = subject
            msg['From'] = from_email
            msg['To'] = to_email
            
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(from_email, smtp_password)
                server.sendmail(from_email, [to_email], msg.as_string())
                
            NotificationService.log_notification(
                recipient_name=recipient_name,
                recipient_contact=to_email,
                msg_type=msg_type,
                channel="Email",
                message=full_msg_content,
                status="Sent"
            )
            return True, "Email sent successfully."
        except Exception as e:
            NotificationService.log_notification(
                recipient_name=recipient_name,
                recipient_contact=to_email,
                msg_type=msg_type,
                channel="Email",
                message=full_msg_content,
                status=f"Error: {str(e)[:15]}"
            )
            return False, str(e)

    @staticmethod
    def send_absent_notification(student, date_str):
        """
        Sends absent notification to parent's mobile (WhatsApp) and email.
        """
        # WhatsApp Message Content
        parent_whatsapp_msg = (
            f"Dear Parent,\n\n"
            f"Your ward was absent today.\n\n"
            f"Student Name: {student.name}\n"
            f"Register Number: {student.email}\n"
            f"Department: {student.department_or_class}\n"
            f"Year: {student.year or 'N/A'}\n"
            f"Date: {date_str}\n\n"
            f"Please contact the department if required.\n\n"
            f"Thank you."
        )
        
        # Email Message Content
        subject = f"Absence Notification: {student.name} ({student.email})"
        
        # Send both
        parent_contact = student.parent_mobile_number if student.parent_mobile_number else "+1234567890"
        wa_success, wa_info = NotificationService.send_whatsapp(
            to_number=parent_contact,
            message=parent_whatsapp_msg,
            recipient_name=f"Parent of {student.name}",
            msg_type="Parent Absent Alert"
        )
        
        email_success = True
        if student.email:
            email_success, email_info = NotificationService.send_email(
                to_email=student.email,
                subject=subject,
                body_text=parent_whatsapp_msg,
                recipient_name=f"Parent of {student.name}",
                msg_type="Parent Absent Alert"
            )
            
        return wa_success and email_success

    @staticmethod
    def send_teacher_summary_notification(teacher, date_str, stats):
        """
        Sends daily attendance summary to teacher via WhatsApp and Email.
        """
        # Message Content
        summary_msg = (
            f"Dear {teacher.name},\n\n"
            f"Here is the Daily Attendance Summary for {date_str}.\n\n"
            f"Total Students: {stats['total']}\n"
            f"Present: {stats['present']}\n"
            f"Absent: {stats['absent']}\n"
            f"Attendance Rate: {stats['percentage']:.1f}%\n\n"
            f"You can view the full details and make any corrections by logging into the college management website.\n\n"
            f"Thank you."
        )
        
        subject = f"Daily Attendance Summary - {date_str}"
        
        # Send both
        wa_success, _ = NotificationService.send_whatsapp(
            to_number=teacher.email, # For mock we can log contact. In real we would need mobile, let's use email as simulated contact.
            message=summary_msg,
            recipient_name=teacher.name,
            msg_type="Teacher Daily Summary"
        )
        
        email_success, _ = NotificationService.send_email(
            to_email=teacher.email,
            subject=subject,
            body_text=summary_msg,
            recipient_name=teacher.name,
            msg_type="Teacher Daily Summary"
        )
        
        return wa_success and email_success

    @staticmethod
    def send_whatsapp_batch_task(app, session_id, advisor_log_id, parent_log_ids):
        """
        Executes as an asynchronous task inside the app context.
        Sends WhatsApp notifications to advisor and parents.
        Updates statuses from Pending to Sent or Failed.
        """
        with app.app_context():
            from models import db, NotificationLog, AttendanceSession
            import requests

            # 1. Fetch config parameters
            account_sid = app.config.get('TWILIO_ACCOUNT_SID', '')
            auth_token = app.config.get('TWILIO_AUTH_TOKEN', '')
            from_number = app.config.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')

            def dispatch_single_msg(log_rec):
                if not log_rec or log_rec.status != 'Pending':
                    return
                
                # Check for simulation mode
                if not account_sid or not auth_token:
                    log_rec.status = 'Simulated'
                    db.session.commit()
                    return
                
                to_num = log_rec.recipient_contact
                to_whatsapp = f"whatsapp:{to_num}" if not to_num.startswith("whatsapp:") else to_num
                
                try:
                    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
                    data = {
                        "From": from_number,
                        "To": to_whatsapp,
                        "Body": log_rec.message
                    }
                    response = requests.post(url, data=data, auth=(account_sid, auth_token))
                    
                    if response.status_code in [200, 201]:
                        res_data = response.json()
                        log_rec.status = 'Sent'
                        log_rec.provider_message_id = res_data.get('sid')
                    else:
                        log_rec.status = 'Failed'
                        log_rec.error_details = f"Twilio API Error ({response.status_code}): {response.text}"
                except Exception as e:
                    log_rec.status = 'Failed'
                    log_rec.error_details = f"Exception: {str(e)}"
                
                db.session.commit()

            # Process Advisor Log
            if advisor_log_id:
                try:
                    advisor_log = NotificationLog.query.get(advisor_log_id)
                    if advisor_log:
                        dispatch_single_msg(advisor_log)
                except Exception as ex:
                    print(f"Error processing advisor log: {ex}")

            # Process Parent Logs
            parent_failed = False
            for parent_id in parent_log_ids:
                try:
                    parent_log = NotificationLog.query.get(parent_id)
                    if parent_log:
                        # If already failed during validation (e.g. missing parent phone), skip Twilio call
                        if parent_log.status == 'Failed':
                            parent_failed = True
                            continue
                        
                        dispatch_single_msg(parent_log)
                        if parent_log.status == 'Failed':
                            parent_failed = True
                except Exception as ex:
                    parent_failed = True
                    try:
                        parent_log = NotificationLog.query.get(parent_id)
                        if parent_log:
                            parent_log.status = 'Failed'
                            parent_log.error_details = f"Unhandled exception: {str(ex)}"
                            db.session.commit()
                    except Exception as commit_ex:
                        print(f"Error saving failed state: {commit_ex}")

            # Update AttendanceSession status
            try:
                session_rec = AttendanceSession.query.get(session_id)
                if session_rec:
                    # Advisor status
                    adv_status = 'Sent'
                    if advisor_log_id:
                        adv_rec = NotificationLog.query.get(advisor_log_id)
                        if adv_rec:
                            adv_status = adv_rec.status

                    if adv_status == 'Failed' or parent_failed:
                        session_rec.report_status = 'Failed' if adv_status == 'Failed' and parent_failed else 'Partial Failure'
                    elif adv_status == 'Simulated':
                        session_rec.report_status = 'Simulated'
                    else:
                        session_rec.report_status = 'Sent'
                    db.session.commit()
            except Exception as ex:
                print(f"Error updating session status: {ex}")
