import os
import cv2
import numpy as np
from flask import current_app

class FaceService:
    @staticmethod
    def get_detector():
        """Load Haar Cascade face detector."""
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        return cv2.CascadeClassifier(cascade_path)

    @staticmethod
    def get_recognizer(user_type):
        """Create LBPH Face Recognizer and load trained model if exists."""
        recognizer = cv2.face.LBPHFaceRecognizer_create()
        
        trainer_path = (
            current_app.config['STUDENT_TRAINER_PATH'] 
            if user_type == 'student' 
            else current_app.config['TEACHER_TRAINER_PATH']
        )
        
        if os.path.exists(trainer_path):
            try:
                recognizer.read(trainer_path)
            except Exception as e:
                print(f"Error reading trainer file {trainer_path}: {e}")
                return None
            return recognizer
        return None

    @staticmethod
    def save_face_snapshots(image_bgr, user_type, user_id, snapshot_index):
        """
        Detects face, crops it to 200x200 grayscale, and saves it.
        Returns True if a face was detected and saved, else False.
        """
        detector = FaceService.get_detector()
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5, minSize=(60, 60))
        
        if len(faces) == 0:
            return False, "No face detected in snapshot."
        
        # Take the largest face if multiple detected
        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        x, y, w, h = faces[0]
        
        # Crop and resize
        face_crop = gray[y:y+h, x:x+w]
        face_resized = cv2.resize(face_crop, (200, 200), interpolation=cv2.INTER_AREA)
        
        # Save path
        parent_dir = (
            current_app.config['STUDENT_FACES_DIR'] 
            if user_type == 'student' 
            else current_app.config['TEACHER_FACES_DIR']
        )
        user_dir = os.path.join(parent_dir, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        
        file_path = os.path.join(user_dir, f"{snapshot_index}.jpg")
        cv2.imwrite(file_path, face_resized)
        
        return True, f"Snapshot {snapshot_index} saved successfully."

    @staticmethod
    def train_model(user_type):
        """
        Loads cropped face images, trains the LBPH model, and saves the trainer file.
        Returns (success_boolean, message)
        """
        parent_dir = (
            current_app.config['STUDENT_FACES_DIR'] 
            if user_type == 'student' 
            else current_app.config['TEACHER_FACES_DIR']
        )
        
        trainer_path = (
            current_app.config['STUDENT_TRAINER_PATH'] 
            if user_type == 'student' 
            else current_app.config['TEACHER_TRAINER_PATH']
        )
        
        face_images = []
        labels = []
        
        if not os.path.exists(parent_dir):
            return False, "Faces directory does not exist."
            
        # Walk through user directories
        for user_id_str in os.listdir(parent_dir):
            user_dir = os.path.join(parent_dir, user_id_str)
            if not os.path.isdir(user_dir):
                continue
                
            try:
                user_id = int(user_id_str)
            except ValueError:
                continue # Directory name must be numeric ID
                
            for filename in os.listdir(user_dir):
                if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                    img_path = os.path.join(user_dir, filename)
                    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        # Ensure correct dimensions (200x200)
                        if img.shape != (200, 200):
                            img = cv2.resize(img, (200, 200))
                        face_images.append(img)
                        labels.append(user_id)
                        
        if len(face_images) == 0:
            # If trainer exists but we delete all images, remove the trainer
            if os.path.exists(trainer_path):
                os.remove(trainer_path)
            return False, f"No images found to train for {user_type}s."
            
        try:
            recognizer = cv2.face.LBPHFaceRecognizer_create()
            recognizer.train(face_images, np.array(labels))
            recognizer.save(trainer_path)
            return True, f"Trained model for {user_type}s with {len(face_images)} images."
        except Exception as e:
            return False, f"Training error: {str(e)}"

    @staticmethod
    def predict_face(image_bgr, user_type):
        """
        Detects faces in a frame, feeds them to the loaded LBPH recognizer.
        Returns list of dicts: [{'id': user_id, 'confidence': conf, 'box': [x, y, w, h]}]
        Confidence represents distance in LBPH: 0 is a perfect match.
        Lower value = better match.
        """
        detector = FaceService.get_detector()
        recognizer = FaceService.get_recognizer(user_type)
        
        if recognizer is None:
            return [] # Model not trained or missing
            
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5, minSize=(60, 60))
        
        results = []
        
        for (x, y, w, h) in faces:
            face_crop = gray[y:y+h, x:x+w]
            face_resized = cv2.resize(face_crop, (200, 200))
            
            try:
                user_id, confidence = recognizer.predict(face_resized)
                results.append({
                    'id': user_id,
                    'confidence': float(confidence), # distance metric
                    'box': [int(x), int(y), int(w), int(h)]
                })
            except Exception as e:
                print(f"Error during recognition predict: {e}")
                
        return results
