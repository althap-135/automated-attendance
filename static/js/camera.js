let mediaStream = null;
let scanIntervalId = null;
let isScanning = false;

// Initialize camera stream
async function startCamera(videoElement) {
    try {
        if (mediaStream) {
            stopCamera();
        }
        
        mediaStream = await navigator.mediaDevices.getUserMedia({
            video: { 
                width: { ideal: 640 },
                height: { ideal: 480 },
                facingMode: "user"
            },
            audio: false
        });
        
        videoElement.srcObject = mediaStream;
        await videoElement.play();
        return true;
    } catch (err) {
        console.error("Error accessing webcam: ", err);
        alert("Camera Access Error: Please ensure you have given camera permissions to this page.");
        return false;
    }
}

// Stop camera stream
function stopCamera() {
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }
    stopScanning();
}

// Starts periodic face scanning
function startScanning(videoElement, overlayCanvas, userType, onMatch, onNoMatch) {
    if (isScanning) return;
    isScanning = true;
    
    const ctx = overlayCanvas.getContext('2d');
    
    // Set overlay canvas display size matching video element
    const resizeOverlay = () => {
        overlayCanvas.width = videoElement.clientWidth;
        overlayCanvas.height = videoElement.clientHeight;
    };
    resizeOverlay();
    window.addEventListener('resize', resizeOverlay);
    
    // Offscreen canvas for capturing raw base64 frame
    const captureCanvas = document.createElement('canvas');
    const captureCtx = captureCanvas.getContext('2d');
    
    // Add scanning class for UI overlay animations
    videoElement.parentElement.classList.add('scanning');
    
    scanIntervalId = setInterval(async () => {
        if (!isScanning) return;
        
        // Grab frame dimensions
        const vw = videoElement.videoWidth;
        const vh = videoElement.videoHeight;
        
        if (vw === 0 || vh === 0) return;
        
        captureCanvas.width = 400; // Resize down for faster network transfer & processing
        captureCanvas.height = (vh / vw) * 400;
        
        // Draw frame to offscreen canvas
        captureCtx.drawImage(videoElement, 0, 0, captureCanvas.width, captureCanvas.height);
        const base64Image = captureCanvas.toDataURL('image/jpeg', 0.85);
        
        try {
            const response = await fetch('/api/attendance/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    image: base64Image,
                    user_type: userType
                })
            });
            
            const result = await response.json();
            
            // Clear previous overlays
            ctx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
            
            if (result.success) {
                // If there's a match, draw box and stop scanning (unless it's already marked)
                if (result.box) {
                    drawFaceBox(ctx, result.box, vw, vh, overlayCanvas.width, overlayCanvas.height, result.student ? result.student.name : result.teacher.name, true);
                }
                
                // Play sound or fire success callback
                onMatch(result);
            } else {
                // Draw red/orange box for detected face that wasn't recognized
                if (result.box) {
                    drawFaceBox(ctx, result.box, vw, vh, overlayCanvas.width, overlayCanvas.height, "Analyzing Face...", false);
                }
                onNoMatch(result);
            }
        } catch (error) {
            console.error("Scan API error:", error);
        }
    }, 1000); // Check every 1 second
}

// Stops scanning timer
function stopScanning() {
    isScanning = false;
    if (scanIntervalId) {
        clearInterval(scanIntervalId);
        scanIntervalId = null;
    }
    const elements = document.querySelectorAll('.camera-container');
    elements.forEach(el => el.classList.remove('scanning'));
}

// Draws a tracking box and details over the face in overlay canvas
function drawFaceBox(ctx, box, srcW, srcH, destW, destH, label, isSuccess) {
    const [x, y, w, h] = box;
    
    // Scale coordinates from capture resolution to display container size
    const scaleX = destW / srcW;
    const scaleY = destH / srcH;
    
    const dx = x * scaleX;
    const dy = y * scaleY;
    const dw = w * scaleX;
    const dh = h * scaleY;
    
    ctx.lineWidth = 3;
    ctx.strokeStyle = isSuccess ? '#10b981' : '#f59e0b'; // green or orange
    
    // Draw rounded corner bracket paths
    const len = Math.min(dw, dh) * 0.25;
    
    ctx.beginPath();
    // Top Left
    ctx.moveTo(dx + len, dy); ctx.lineTo(dx, dy); ctx.lineTo(dx, dy + len);
    // Top Right
    ctx.moveTo(dx + dw - len, dy); ctx.lineTo(dx + dw, dy); ctx.lineTo(dx + dw, dy + len);
    // Bottom Left
    ctx.moveTo(dx, dy + dh - len); ctx.lineTo(dx, dy + dh); ctx.lineTo(dx + len, dy + dh);
    // Bottom Right
    ctx.moveTo(dx + dw - len, dy + dh); ctx.lineTo(dx + dw, dy + dh); ctx.lineTo(dx + dw, dy + dh - len);
    ctx.stroke();
    
    // Draw label
    ctx.font = '14px Outfit, sans-serif';
    ctx.fillStyle = isSuccess ? '#10b981' : '#f59e0b';
    ctx.fillText(label, dx, dy - 8);
}

// Capture single snapshot for enrollment
async function captureEnrollmentSnapshot(videoElement, userId, userType, snapshotIndex) {
    const captureCanvas = document.createElement('canvas');
    const captureCtx = captureCanvas.getContext('2d');
    
    const vw = videoElement.videoWidth;
    const vh = videoElement.videoHeight;
    
    if (vw === 0 || vh === 0) {
        return { success: false, message: "Camera feed not ready." };
    }
    
    // High quality frame for enrollment
    captureCanvas.width = 640;
    captureCanvas.height = 480;
    
    captureCtx.drawImage(videoElement, 0, 0, captureCanvas.width, captureCanvas.height);
    const base64Image = captureCanvas.toDataURL('image/jpeg', 0.95);
    
    try {
        const response = await fetch('/api/settings/enroll', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                image: base64Image,
                user_type: userType,
                user_id: userId,
                snapshot_index: snapshotIndex
            })
        });
        return await response.json();
    } catch (error) {
        console.error("Enroll API error:", error);
        return { success: false, message: "Network error during capture." };
    }
}
