document.addEventListener('DOMContentLoaded', () => {
    // ---------------- COMMON LAYOUT & MODALS ----------------
    
    // Auto-update copyright/current date in footer if needed
    const currentYear = new Date().getFullYear();
    const yearEl = document.getElementById('footer-year');
    if (yearEl) yearEl.textContent = currentYear;

    // Seeding database from UI
    const btnSeed = document.getElementById('btn-seed-db');
    if (btnSeed) {
        btnSeed.addEventListener('click', async () => {
            if (!confirm("Are you sure you want to seed the database with sample students and teachers?")) return;
            
            btnSeed.disabled = true;
            btnSeed.textContent = "Seeding...";
            
            try {
                const res = await fetch('/api/settings/seed', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast(data.message, "success");
                    setTimeout(() => window.location.reload(), 2000);
                } else {
                    showToast(data.message || "Failed to seed database.", "error");
                    btnSeed.disabled = false;
                    btnSeed.textContent = "Seed Database with Samples";
                }
            } catch (err) {
                console.error(err);
                showToast("Failed to seed database.", "error");
                btnSeed.disabled = false;
                btnSeed.textContent = "Seed Database with Samples";
            }
        });
    }

    // Modal helpers
    window.openModal = function(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.add('active');
        }
    };

    window.closeModal = function(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.remove('active');
        }
    };

    // Close modal when clicking overlay
    const overlays = document.querySelectorAll('.modal-overlay');
    overlays.forEach(overlay => {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                overlay.classList.remove('active');
            }
        });
    });


    // ---------------- LIVE SEARCH MODULE ----------------
    
    const searchInput = document.getElementById('student-search-input');
    const searchResults = document.getElementById('search-suggest-list');
    
    if (searchInput && searchResults) {
        searchInput.addEventListener('input', async () => {
            const query = searchInput.value.trim();
            if (query.length < 1) {
                searchResults.classList.remove('active');
                searchResults.innerHTML = '';
                return;
            }
            
            try {
                const res = await fetch(`/api/student/search?q=${encodeURIComponent(query)}`);
                const students = await res.json();
                
                if (students.length === 0) {
                    searchResults.innerHTML = `<li class="text-center text-muted">No students found</li>`;
                    searchResults.classList.add('active');
                    return;
                }
                
                const label = (window.INSTITUTION_TYPE === 'School') ? 'Class' : 'Dept';
                const yearStr = (window.INSTITUTION_TYPE === 'School') ? '' : ` (${s.year || 'N/A'})`;
                searchResults.innerHTML = students.map(s => `
                    <li data-id="${s.id}">
                        <div class="res-name">${s.name}</div>
                        <div class="res-sub">Email: ${s.email} | ${label}: ${s.department_or_class}${yearStr}</div>
                    </li>
                `).join('');
                searchResults.classList.add('active');
                
                // Add click handler to list items
                searchResults.querySelectorAll('li').forEach(item => {
                    item.addEventListener('click', () => {
                        const id = item.getAttribute('data-id');
                        if (id) {
                            window.location.href = `/student/${id}`;
                        }
                    });
                });
            } catch (err) {
                console.error("Error searching students:", err);
            }
        });
        
        // Hide search suggestions on document click outside
        document.addEventListener('click', (e) => {
            if (e.target !== searchInput && e.target !== searchResults) {
                searchResults.classList.remove('active');
            }
        });
    }


    // ---------------- TEACHER CORRECTION MODULE ----------------
    
    // Set correction details in modal
    let activeCorrectionRecord = null;
    
    window.openCorrectionModal = function(studentId, studentName, regNum, dateStr, currentStatus) {
        if (currentStatus === 'Present') {
            showToast("Attendance status is already Present. No correction is allowed.", "info");
            return;
        }
        
        activeCorrectionRecord = { studentId, dateStr };
        
        document.getElementById('correct-student-name').textContent = studentName;
        document.getElementById('correct-student-reg').textContent = regNum;
        document.getElementById('correct-student-date').textContent = dateStr;
        
        openModal('correction-modal');
    };
    
    const submitCorrectionBtn = document.getElementById('btn-submit-correction');
    if (submitCorrectionBtn) {
        submitCorrectionBtn.addEventListener('click', async () => {
            if (!activeCorrectionRecord) return;
            
            const reason = document.getElementById('correction-reason').value;
            if (!reason) {
                showToast("Please select a valid reason for correction.", "error");
                return;
            }
            
            if (!confirm(`Are you sure you want to correct attendance to PRESENT for this student?\nReason: ${reason}`)) return;
            
            submitCorrectionBtn.disabled = true;
            submitCorrectionBtn.textContent = "Updating...";
            
            try {
                const res = await fetch('/api/attendance/correct', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        student_id: activeCorrectionRecord.studentId,
                        date: activeCorrectionRecord.dateStr,
                        new_status: 'Present',
                        reason: reason
                    })
                });
                
                if (res.status === 403) {
                    showToast("Staff Face Authentication required. Redirecting...", "warning");
                    setTimeout(() => {
                        window.location.href = `/teacher/auth?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`;
                    }, 1500);
                    return;
                }
                
                const data = await res.json();
                
                if (data.success) {
                    window.location.href = "/teacher/auth?status=success";
                } else {
                    showToast(`Error: ${data.message}`, "error");
                    submitCorrectionBtn.disabled = false;
                    submitCorrectionBtn.textContent = "Confirm Update";
                }
            } catch (err) {
                console.error(err);
                showToast("Failed to update attendance.", "error");
                submitCorrectionBtn.disabled = false;
                submitCorrectionBtn.textContent = "Confirm Update";
            }
        });
    }

    // Submit Review Attendance
    const btnSubmitReview = document.getElementById('btn-submit-review');
    if (btnSubmitReview) {
        btnSubmitReview.addEventListener('click', async () => {
            if (!confirm("This will mark all remaining students for today as ABSENT and send a review report to the Class Advisor.\n\nAre you sure you want to submit today's attendance for review?")) return;
            
            btnSubmitReview.disabled = true;
            btnSubmitReview.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Submitting...`;
            
            try {
                const res = await fetch('/api/attendance/submit_review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ date: new Date().toISOString().split('T')[0] })
                });
                
                if (res.status === 403) {
                    showToast("Staff Face Authentication required. Redirecting...", "warning");
                    setTimeout(() => {
                        window.location.href = `/teacher/auth?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`;
                    }, 1500);
                    return;
                }
                
                const data = await res.json();
                
                if (data.success) {
                    showToast(data.message, "success");
                    setTimeout(() => window.location.reload(), 2000);
                } else {
                    showToast(`Error: ${data.message}`, "error");
                    btnSubmitReview.disabled = false;
                    btnSubmitReview.innerHTML = `<i class="fas fa-paper-plane"></i> Send to Advisor for Review`;
                }
            } catch (err) {
                console.error(err);
                showToast("Network error. Review submission failed.", "error");
                btnSubmitReview.disabled = false;
                btnSubmitReview.innerHTML = `<i class="fas fa-paper-plane"></i> Send to Advisor for Review`;
            }
        });
    }

    // Approve & Finalize Attendance
    const btnApproveFinalize = document.getElementById('btn-approve-finalize');
    if (btnApproveFinalize) {
        btnApproveFinalize.addEventListener('click', async () => {
            if (!confirm("This will permanently lock today's attendance and trigger WhatsApp alerts to the parents of remaining absent students.\n\nAre you sure you want to approve and finalize today's attendance?")) return;
            
            btnApproveFinalize.disabled = true;
            btnApproveFinalize.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Finalizing...`;
            
            try {
                const res = await fetch('/api/attendance/approve_finalize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ date: new Date().toISOString().split('T')[0] })
                });
                
                if (res.status === 403) {
                    showToast("Staff Face Authentication required. Redirecting...", "warning");
                    setTimeout(() => {
                        window.location.href = `/teacher/auth?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`;
                    }, 1500);
                    return;
                }
                
                const data = await res.json();
                
                if (data.success) {
                    showToast(data.message, "success");
                    setTimeout(() => window.location.reload(), 2000);
                } else {
                    showToast(`Error: ${data.message}`, "error");
                    btnApproveFinalize.disabled = false;
                    btnApproveFinalize.innerHTML = `<i class="fas fa-check-double"></i> Approve & Finalize Attendance`;
                }
            } catch (err) {
                console.error(err);
                showToast("Network error. Finalization failed.", "error");
                btnApproveFinalize.disabled = false;
                btnApproveFinalize.innerHTML = `<i class="fas fa-check-double"></i> Approve & Finalize Attendance`;
            }
        });
    }


    // ---------------- CHART ANALYTICS MODULE ----------------
    
    const overallChartCanvas = document.getElementById('overallAttendanceChart');
    const deptChartCanvas = document.getElementById('deptAttendanceChart');
    const trendChartCanvas = document.getElementById('trendAttendanceChart');
    
    if (overallChartCanvas || deptChartCanvas || trendChartCanvas) {
        loadAnalyticsCharts();
    }
    
    async function loadAnalyticsCharts() {
        try {
            const res = await fetch('/api/analytics/data');
            const data = await res.json();
            
            // 1. Overall Pie Chart
            if (overallChartCanvas) {
                const present = data.summary.present;
                const absent = data.summary.absent;
                
                if (present === 0 && absent === 0) {
                    // Draw empty state
                    overallChartCanvas.parentElement.innerHTML = `<div class="text-center text-muted" style="padding: 40px 0;">No attendance records found to display. Seeding data is recommended.</div>`;
                } else {
                    new Chart(overallChartCanvas, {
                        type: 'doughnut',
                        data: {
                            labels: ['Present', 'Absent'],
                            datasets: [{
                                data: [present, absent],
                                backgroundColor: ['#10b981', '#ef4444'],
                                borderWidth: 0,
                                hoverOffset: 4
                            }]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: {
                                legend: {
                                    position: 'bottom',
                                    labels: { color: '#9ca3af', font: { family: 'Outfit' } }
                                }
                            },
                            cutout: '70%'
                        }
                    });
                }
            }
            
            // 2. Department-wise Bar Chart
            if (deptChartCanvas) {
                new Chart(deptChartCanvas, {
                    type: 'bar',
                    data: {
                        labels: data.departments.labels,
                        datasets: [{
                            label: 'Present Rate (%)',
                            data: data.departments.data,
                            backgroundColor: 'rgba(37, 99, 235, 0.75)',
                            borderColor: '#2563eb',
                            borderWidth: 1,
                            borderRadius: 6
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            x: {
                                grid: { display: false },
                                ticks: { color: '#9ca3af', font: { family: 'Outfit' } }
                            },
                            y: {
                                grid: { color: 'rgba(255, 255, 255, 0.05)' },
                                ticks: { color: '#9ca3af', font: { family: 'Outfit' } },
                                min: 0,
                                max: 100
                            }
                        }
                    }
                });
            }
            
            // 3. Weekly Attendance Trend Line Chart
            if (trendChartCanvas) {
                new Chart(trendChartCanvas, {
                    type: 'line',
                    data: {
                        labels: data.trend.labels,
                        datasets: [{
                            label: 'Attendance Rate (%)',
                            data: data.trend.data,
                            borderColor: '#10b981',
                            backgroundColor: 'rgba(16, 185, 129, 0.1)',
                            borderWidth: 3,
                            fill: true,
                            tension: 0.4,
                            pointBackgroundColor: '#10b981',
                            pointRadius: 4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            x: {
                                grid: { display: false },
                                ticks: { color: '#9ca3af', font: { family: 'Outfit' } }
                            },
                            y: {
                                grid: { color: 'rgba(255, 255, 255, 0.05)' },
                                ticks: { color: '#9ca3af', font: { family: 'Outfit' } },
                                min: 0,
                                max: 100
                            }
                        }
                    }
                });
            }
        } catch (err) {
            console.error("Error drawing charts: ", err);
        }
    }
});
