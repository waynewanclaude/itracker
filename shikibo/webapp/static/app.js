// WebApp Application Logic

let activeThreadId = null;
let currentDraftId = null;
let config = {};
let lastMessageIds = [];
let pollingPaused = false;
let lastUpdateTime = Date.now();

// On startup
document.addEventListener("DOMContentLoaded", async () => {
    await loadConfig();
    await refreshAll();
    
    if (config.use_fs_events) {
        console.log("Filesystem events enabled. Hooking up EventSource to /api/events");
        const eventSource = new EventSource("/api/events");
        eventSource.onmessage = async (event) => {
            if (event.data === "refresh") {
                if (pollingPaused) return;
                await refreshAll();
                if (activeThreadId) {
                    await loadThreadMessages(activeThreadId);
                }
            }
        };
        eventSource.onerror = (e) => {
            console.error("EventSource failed:", e);
        };
    } else {
        // Set up recurring poll (every 5 seconds) to fetch messages, pending outbox, and receipts
        setInterval(pollUpdates, 5000);
    }
    
    // Update the last update elapsed time display every second
    setInterval(updateElapsedText, 1000);
    updateElapsedText();
    
    // Bind UI actions
    document.getElementById("btn-new-thread").addEventListener("click", openNewThreadModal);
    document.getElementById("btn-close-modal").addEventListener("click", closeNewThreadModal);
    document.getElementById("btn-submit-thread").addEventListener("click", submitNewThread);
    document.getElementById("btn-scan").addEventListener("click", runCoordinatorScan);
    document.getElementById("btn-add-attachment").addEventListener("click", () => {
        document.getElementById("composer-file-input").click();
    });
    document.getElementById("composer-file-input").addEventListener("change", handleFileUpload);
    document.getElementById("btn-publish").addEventListener("click", publishMessage);
    document.getElementById("btn-toggle-lock").addEventListener("click", cycleThreadStatus);
    document.getElementById("btn-archive").addEventListener("click", archiveThread);
    document.getElementById("btn-pause-resume").addEventListener("click", togglePauseResume);
    
    // Enter to publish, Shift+Enter to newline
    document.getElementById("composer-body").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            const body = document.getElementById("composer-body").value;
            if (body && body.trim()) {
                publishMessage();
            }
        }
    });
    document.getElementById("composer-body").addEventListener("input", updatePublishButtonState);
    document.getElementById("thread-title-input").addEventListener("input", updateThreadId);
});

function getFolderHash(str) {
    let hash = 5381;
    for (let i = 0; i < str.length; i++) {
        hash = ((hash << 5) + hash) + str.charCodeAt(i);
    }
    return hash;
}

function hslToRgb(h, s, l) {
    s /= 100;
    l /= 100;
    const k = n => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

function updateTitleBarColor(folderName) {
    const bar = document.getElementById("workspace-title-bar");
    if (!bar) return;
    const hash = getFolderHash(folderName);
    const hue = Math.abs(hash) % 360;
    const saturation = 65;
    const lightness = 45;
    bar.style.backgroundColor = `hsl(${hue}, ${saturation}%, ${lightness}%)`;
    
    const rgb = hslToRgb(hue, saturation, lightness);
    const brightness = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000;
    const textColor = brightness > 125 ? "#111111" : "#ffffff";
    const secColor = brightness > 125 ? "rgba(0,0,0,0.7)" : "rgba(255,255,255,0.85)";
    
    document.getElementById("folder-name").style.color = textColor;
    document.getElementById("folder-path").style.color = secColor;
}

async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        config = await res.json();
        const userText = config.role ? `${config.display_name} (${config.user_id}/${config.role})` : `${config.display_name} (${config.user_id})`;
        document.getElementById("user-badge").textContent = userText;
        
        // Display root directory info
        const rootDir = config.root_dir || "";
        const folderName = rootDir.split(/[/\\]/).pop() || rootDir;
        document.getElementById("folder-name").textContent = folderName;
        document.getElementById("folder-path").textContent = rootDir;
        
        // Update Title Bar Color dynamically
        updateTitleBarColor(folderName);
        recordUIUpdate();
    } catch (e) {
        console.error("Failed to load config", e);
    }
}

async function refreshAll() {
    await loadThreads();
    await loadArchivedThreads();
    await loadPending();
    recordUIUpdate();
}

async function pollUpdates() {
    if (pollingPaused) return;
    if (activeThreadId) {
        await loadThreadMessages(activeThreadId);
    }
    await loadArchivedThreads();
    await loadPending();
    recordUIUpdate();
}

async function togglePauseResume() {
    const btn = document.getElementById("btn-pause-resume");
    pollingPaused = !pollingPaused;
    
    if (pollingPaused) {
        btn.textContent = "Resume Updates";
        btn.classList.add("btn-warning");
        btn.classList.remove("btn-secondary");
        showScanStatus("Updates paused");
    } else {
        btn.textContent = "Pause Updates";
        btn.classList.add("btn-secondary");
        btn.classList.remove("btn-warning");
        showScanStatus("Updates resumed");
        await refreshAll();
        if (activeThreadId) {
            await loadThreadMessages(activeThreadId);
        }
    }
    updateElapsedText();
}

function recordUIUpdate() {
    lastUpdateTime = Date.now();
    updateElapsedText();
}

function updateElapsedText() {
    const elapsedSeconds = Math.floor((Date.now() - lastUpdateTime) / 1000);
    const container = document.getElementById("last-update-elapsed");
    if (container) {
        if (pollingPaused) {
            container.textContent = `Last update: ${elapsedSeconds}s ago (Paused)`;
        } else {
            container.textContent = `Last update: ${elapsedSeconds}s ago`;
        }
    }
}

// --- Thread operations ---

async function loadThreads() {
    try {
        const res = await fetch("/api/threads");
        const threads = await res.json();
        const container = document.getElementById("thread-list");
        container.innerHTML = "";
        
        if (threads.length === 0) {
            container.innerHTML = '<div class="empty-state" style="font-size:0.8rem; padding:10px;">No threads found</div>';
            return;
        }
        
        threads.forEach(t => {
            const item = document.createElement("div");
            item.className = `thread-item ${t.thread_id === activeThreadId ? 'active' : ''} ${t.status === 'DONE' ? 'done' : ''}`;
            item.dataset.threadId = t.thread_id;
            
            // Truncate thread ID to first 16 hex digits (stripping T_ prefix)
            let displayId = t.thread_id;
            if (displayId.startsWith("T_")) {
                displayId = displayId.slice(2);
            }
            displayId = displayId.slice(0, 16);
            
            item.textContent = `${t.title} (${displayId})`;
            item.addEventListener("click", () => selectThread(t.thread_id, t.title, t.description_md || "", t.status, t.hostname, t.created_at, t.creator_user_id));
            container.appendChild(item);
        });
    } catch (e) {
        console.error("Failed to load threads", e);
    }
}

async function loadArchivedThreads() {
    try {
        const res = await fetch("/api/threads/archived");
        const threads = await res.json();
        const container = document.getElementById("archived-thread-list");
        if (!container) return;
        container.innerHTML = "";
        
        if (threads.length === 0) {
            container.innerHTML = '<div class="empty-state" style="font-size:0.8rem; padding:10px;">No archived threads</div>';
            return;
        }
        
        threads.forEach(t => {
            const item = document.createElement("div");
            item.className = `thread-item archived ${t.thread_id === activeThreadId ? 'active' : ''}`;
            item.dataset.threadId = t.thread_id;
            
            let displayId = t.thread_id;
            if (displayId.startsWith("T_")) {
                displayId = displayId.slice(2);
            }
            displayId = displayId.slice(0, 16);
            
            item.textContent = `${t.title} (${displayId})`;
            item.addEventListener("click", () => selectThread(t.thread_id, t.title, t.description_md || "", t.status, t.hostname, t.created_at, t.creator_user_id));
            container.appendChild(item);
        });
    } catch (e) {
        console.error("Failed to load archived threads", e);
    }
}

function selectThread(threadId, title, desc, status, hostname, createdAt, creatorUserId) {
    activeThreadId = threadId;
    lastMessageIds = []; // Clear message IDs on thread change to force scrolling
    
    // Highlight active in list
    document.querySelectorAll(".thread-item").forEach(item => {
        if (item.dataset.threadId === threadId) {
            item.classList.add("active");
        } else {
            item.classList.remove("active");
        }
    });
    
    // Show header info
    document.getElementById("active-thread-title").textContent = title;
    document.getElementById("active-thread-desc").textContent = desc || "No description provided";
    
    // Show metadata details in small font
    const metaDiv = document.getElementById("active-thread-meta");
    if (hostname && createdAt) {
        metaDiv.textContent = `Host: ${hostname} | Created (GMT): ${createdAt}`;
        metaDiv.style.display = "block";
    } else {
        metaDiv.style.display = "none";
    }
    
    const pauseBtn = document.getElementById("btn-pause-resume");
    pauseBtn.style.display = "block";
    
    // Ownership checks for status controls (Lock, Archive)
    const currentUser = config.role ? `${config.user_id}/${config.role}` : config.user_id;
    const isOwner = (creatorUserId && creatorUserId === currentUser);
    
    const toggleLockBtn = document.getElementById("btn-toggle-lock");
    const archiveBtn = document.getElementById("btn-archive");
    
    if (isOwner && status !== "ARCHIVED") {
        toggleLockBtn.style.display = "block";
        archiveBtn.style.display = "block";
        
        if (status === "UNLOCK") {
            toggleLockBtn.textContent = "Restrict";
        } else if (status === "RESTRICT") {
            toggleLockBtn.textContent = "Lock";
        } else if (status === "LOCK") {
            toggleLockBtn.textContent = "Unlock";
        }
    } else {
        toggleLockBtn.style.display = "none";
        archiveBtn.style.display = "none";
    }
    
    // Load messages
    loadThreadMessages(threadId);
    
    // Load or initialize draft
    setupDraftForThread(threadId, status, isOwner);
}

async function loadThreadMessages(threadId) {
    try {
        const res = await fetch(`/api/threads/${threadId}/messages`);
        const messages = await res.json();
        const container = document.getElementById("message-timeline");
        
        // Generate IDs for messages in this render
        const messageIds = messages.map(msg => `${msg.source_user_id}_${msg.source_local_message_id}_${msg.local_created_at}`);
        // Check if there are any new messages not in lastMessageIds
        const hasNewMessages = messageIds.some(id => !lastMessageIds.includes(id));
        
        container.innerHTML = "";
        
        if (messages.length === 0) {
            container.innerHTML = '<div class="empty-state">No messages in this thread yet.</div>';
            lastMessageIds = [];
            return;
        }
        
        messages.forEach(msg => {
            const configUserId = config.role ? `${config.user_id}/${config.role}` : config.user_id;
            const isSelf = msg.source_user_id === configUserId;
            const card = document.createElement("div");
            card.className = `message-card ${isSelf ? 'self' : ''}`;
            
            // Header
            const meta = document.createElement("div");
            meta.className = "message-meta";
            
            const timestamp = msg.local_created_at ? new Date(msg.local_created_at).toLocaleString() : "Unknown date";
            const indexInfo = msg.folder_name ? msg.folder_name.split("_")[1] : "";
            
            meta.innerHTML = `
                <span><strong>${msg.source_user_id}</strong> ${indexInfo ? `(#${parseInt(indexInfo)})` : ''}</span>
                <span>${timestamp}</span>
            `;
            card.appendChild(meta);
            
            // Body
            const body = document.createElement("div");
            body.className = "message-body";
            body.textContent = msg.body;
            card.appendChild(body);
            
            // Attachments
            if (msg.attachments && msg.attachments.length > 0) {
                const attachDiv = document.createElement("div");
                attachDiv.className = "message-attachments";
                attachDiv.innerHTML = "<strong>Attachments:</strong><br>";
                
                msg.attachments.forEach(att => {
                    const tag = document.createElement("a");
                    tag.className = "attachment-tag";
                    tag.href = `/api/attachments/${threadId}/${msg.folder_name}/${att.stored_filename}`;
                    tag.target = "_blank";
                    tag.textContent = att.original_filename;
                    attachDiv.appendChild(tag);
                });
                card.appendChild(attachDiv);
            }
            
            container.appendChild(card);
        });
        
        // Auto scroll to bottom only when there are new messages
        if (hasNewMessages) {
            container.scrollTop = container.scrollHeight;
        }
        
        // Update cached IDs
        lastMessageIds = messageIds;
        recordUIUpdate();
    } catch (e) {
        console.error("Failed to load thread messages", e);
    }
}

async function cycleThreadStatus() {
    if (!activeThreadId) return;
    
    const btn = document.getElementById("btn-toggle-lock");
    const currentText = btn.textContent;
    let nextStatus = "UNLOCK";
    if (currentText === "Restrict") {
        nextStatus = "RESTRICT";
    } else if (currentText === "Lock") {
        nextStatus = "LOCK";
    } else if (currentText === "Unlock") {
        nextStatus = "UNLOCK";
    }
    
    try {
        const res = await fetch(`/api/threads/${activeThreadId}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: jsonStringify({ status: nextStatus })
        });
        
        if (res.ok) {
            await refreshAll();
            const title = document.getElementById("active-thread-title").textContent;
            const desc = document.getElementById("active-thread-desc").textContent;
            const metaText = document.getElementById("active-thread-meta").textContent;
            let host = "";
            let created = "";
            if (metaText) {
                const parts = metaText.split(" | ");
                if (parts.length === 2) {
                    host = parts[0].replace("Host: ", "");
                    created = parts[1].replace("Created (GMT): ", "");
                }
            }
            const currentUser = config.role ? `${config.user_id}/${config.role}` : config.user_id;
            selectThread(activeThreadId, title, desc, nextStatus, host, created, currentUser);
        } else {
            const err = await res.json();
            alert(`Error updating thread status: ${err.error}`);
        }
    } catch (e) {
        console.error("Failed to update status", e);
    }
}

async function archiveThread() {
    if (!activeThreadId) return;
    if (!confirm("Are you sure you want to archive this thread? It will be locked, zipped, and removed from active threads.")) return;
    
    try {
        const res = await fetch(`/api/threads/${activeThreadId}/archive`, { method: "POST" });
        if (res.ok) {
            alert("Thread successfully archived!");
            activeThreadId = null;
            document.getElementById("active-thread-title").textContent = "Select a thread or create a new one";
            document.getElementById("active-thread-desc").textContent = "";
            document.getElementById("active-thread-meta").style.display = "none";
            document.getElementById("btn-toggle-lock").style.display = "none";
            document.getElementById("btn-archive").style.display = "none";
            document.getElementById("btn-pause-resume").style.display = "none";
            document.getElementById("composer-area").style.display = "none";
            document.getElementById("closed-thread-notice").style.display = "none";
            document.getElementById("message-timeline").innerHTML = '<div class="empty-state">No thread selected</div>';
            
            await refreshAll();
        } else {
            const err = await res.json();
            alert(`Archive failed: ${err.error}`);
        }
    } catch (e) {
        console.error("Failed to archive thread", e);
    }
}

// --- New Thread Modal ---

async function openNewThreadModal() {
    document.getElementById("new-thread-modal").style.display = "flex";
    document.getElementById("thread-title-input").value = "";
    document.getElementById("thread-desc-input").value = "";
    await updateThreadId();
}

async function updateThreadId() {
    const title = document.getElementById("thread-title-input").value.trim();
    try {
        const res = await fetch(`/api/threads/next-id?title=${encodeURIComponent(title)}`);
        if (res.ok) {
            const data = await res.json();
            document.getElementById("thread-id-input").value = data.thread_id;
        }
    } catch (e) {
        console.error("Failed to fetch next thread ID", e);
    }
}

function closeNewThreadModal() {
    document.getElementById("new-thread-modal").style.display = "none";
}

async function submitNewThread() {
    const threadId = document.getElementById("thread-id-input").value.trim();
    const title = document.getElementById("thread-title-input").value.trim();
    const description = document.getElementById("thread-desc-input").value.trim();
    
    if (!threadId || !title) {
        alert("Please specify Thread ID and Title.");
        return;
    }
    
    try {
        const res = await fetch("/api/threads", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: jsonStringify({ thread_id: threadId, title: title, description: description })
        });
        
        if (res.ok) {
            closeNewThreadModal();
            // Reset input values
            document.getElementById("thread-id-input").value = "";
            document.getElementById("thread-title-input").value = "";
            document.getElementById("thread-desc-input").value = "";
            
            await loadThreads();
            const currentUser = config.role ? `${config.user_id}/${config.role}` : config.user_id;
            selectThread(threadId, title, description, "UNLOCK", null, null, currentUser);
        } else {
            const err = await res.json();
            alert(`Failed to create thread: ${err.error}`);
        }
    } catch (e) {
        console.error("Failed to submit new thread", e);
    }
}

// --- Draft & Composer Operations ---

async function setupDraftForThread(threadId, status, isOwner) {
    const noticeEl = document.getElementById("closed-thread-notice");
    if (status === "LOCK") {
        document.getElementById("composer-area").style.display = "none";
        noticeEl.textContent = "This thread is locked. No further messages can be posted.";
        noticeEl.style.display = "block";
        return;
    } else if (status === "ARCHIVED") {
        document.getElementById("composer-area").style.display = "none";
        noticeEl.textContent = "This thread is archived and closed. No further messages can be posted.";
        noticeEl.style.display = "block";
        return;
    } else if (status === "RESTRICT" && !isOwner) {
        document.getElementById("composer-area").style.display = "none";
        noticeEl.textContent = "This thread is restricted. Only the thread creator can post messages.";
        noticeEl.style.display = "block";
        return;
    }
    
    noticeEl.style.display = "none";
    document.getElementById("composer-area").style.display = "block";
    document.getElementById("composer-body").value = "";
    document.getElementById("draft-attachments-list").innerHTML = "";
    currentDraftId = null;
    
    // Check if there is an existing draft for this thread
    try {
        const res = await fetch("/api/drafts");
        const drafts = await res.json();
        const threadDraft = drafts.find(d => d.thread_id === threadId);
        
        if (threadDraft) {
            currentDraftId = threadDraft.draft_id;
            document.getElementById("composer-body").value = threadDraft.body || "";
            renderDraftAttachments(threadDraft.attachments || []);
        } else {
            // Create a new draft
            const createRes = await fetch("/api/drafts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: jsonStringify({ thread_id: threadId, body: "" })
            });
            const data = await createRes.json();
            currentDraftId = data.draft_id;
        }
    } catch (e) {
        console.error("Failed to setup draft", e);
    }
    document.getElementById("composer-body").focus();
    updatePublishButtonState();
}

async function saveDraft() {
    if (!currentDraftId) return;
    const body = document.getElementById("composer-body").value;
    try {
        const res = await fetch(`/api/drafts/${currentDraftId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: jsonStringify({ body: body })
        });
        if (res.ok) {
            showScanStatus("Draft saved locally");
        }
    } catch (e) {
        console.error("Failed to save draft", e);
    }
}

async function handleFileUpload(e) {
    if (!currentDraftId) return;
    const file = e.target.files[0];
    if (!file) return;
    
    const formData = new FormData();
    formData.append("file", file);
    
    showScanStatus("Uploading attachment...");
    try {
        const res = await fetch(`/api/drafts/${currentDraftId}/attachments`, {
            method: "POST",
            body: formData
        });
        
        if (res.ok) {
            const data = await res.json();
            showScanStatus("Attachment uploaded");
            // Reload draft to get updated list
            const draftRes = await fetch("/api/drafts");
            const drafts = await draftRes.json();
            const draft = drafts.find(d => d.draft_id === currentDraftId);
            if (draft) {
                renderDraftAttachments(draft.attachments || []);
            }
        } else {
            const err = await res.json();
            alert(`Upload failed: ${err.error}`);
        }
    } catch (ex) {
        console.error("Upload error", ex);
    } finally {
        e.target.value = ""; // Reset input
    }
}

function renderDraftAttachments(attachments) {
    const container = document.getElementById("draft-attachments-list");
    container.innerHTML = "";
    
    attachments.forEach(att => {
        const item = document.createElement("span");
        item.style.marginRight = "10px";
        item.style.fontSize = "0.8rem";
        item.innerHTML = `
            ${att.original_filename} 
            <a href="#" style="color:var(--danger-color); text-decoration:none;" onclick="deleteAttachment('${att.attachment_id}', event)">[x]</a>
        `;
        container.appendChild(item);
    });
}

async function deleteAttachment(attachId, e) {
    e.preventDefault();
    if (!currentDraftId) return;
    
    try {
        const res = await fetch(`/api/drafts/${currentDraftId}/attachments/${attachId}`, {
            method: "DELETE"
        });
        if (res.ok) {
            // Reload draft attachments
            const draftsRes = await fetch("/api/drafts");
            const drafts = await draftsRes.json();
            const draft = drafts.find(d => d.draft_id === currentDraftId);
            if (draft) {
                renderDraftAttachments(draft.attachments || []);
            }
        }
    } catch (ex) {
        console.error("Delete attachment failed", ex);
    }
}

async function publishMessage() {
    if (!currentDraftId) return;
    
    // First auto-save current body text
    await saveDraft();
    
    try {
        const res = await fetch(`/api/drafts/${currentDraftId}/publish`, {
            method: "POST"
        });
        
        if (res.ok) {
            showScanStatus("Message published to outbox!");
            document.getElementById("composer-body").value = "";
            document.getElementById("draft-attachments-list").innerHTML = "";
            document.getElementById("composer-area").style.display = "none";
            currentDraftId = null;
            
            await refreshAll();
            if (activeThreadId) {
                // Relinquish focus or reinitialize draft
                setupDraftForThread(activeThreadId);
            }
        } else {
            const err = await res.json();
            alert(`Failed to publish: ${err.error}`);
        }
    } catch (e) {
        console.error("Publish failed", e);
    }
}

// --- Outbox and Receipts display ---

async function loadPending() {
    try {
        const res = await fetch("/api/pending");
        const pending = await res.json();
        const container = document.getElementById("pending-list");
        container.innerHTML = "";
        
        if (pending.length === 0) {
            container.innerHTML = '<div class="empty-state">No pending messages</div>';
            return;
        }
        
        pending.forEach(p => {
            const item = document.createElement("div");
            item.className = "util-item";
            
            const timestamp = p.local_created_at ? new Date(p.local_created_at).toLocaleTimeString() : "";
            
            item.innerHTML = `
                <div class="util-item-header">
                    <span>${p.source_local_message_id}</span>
                    <span>${timestamp}</span>
                </div>
                <div class="util-item-body">
                    Thread: ${p.target_thread_id}<br>
                    Attachments: ${p.attachments ? p.attachments.length : 0}
                </div>
            `;
            container.appendChild(item);
        });
    } catch (e) {
        console.error("Failed to load pending outbox", e);
    }
}

// --- Coordinator Trigger ---

async function runCoordinatorScan() {
    showScanStatus("Scanning outboxes...");
    try {
        const res = await fetch("/api/coordinator/scan", { method: "POST" });
        const summary = await res.json();
        
        if (res.ok) {
            let msg = `Done. Processed: ${summary.processed}`;
            if (summary.duplicates > 0) msg += `, Dups: ${summary.duplicates}`;
            if (summary.dead_lettered > 0) msg += `, Dead-Letters: ${summary.dead_lettered}`;
            showScanStatus(msg);
            
            await refreshAll();
            if (activeThreadId) {
                await loadThreadMessages(activeThreadId);
            }
        } else {
            showScanStatus("Scan failed");
            alert(`Coordinator error: ${summary.error}`);
        }
    } catch (e) {
        showScanStatus("Scan error");
        console.error("Coordinator trigger failed", e);
    }
}

function showScanStatus(msg) {
    const el = document.getElementById("scan-status");
    el.textContent = msg;
    // Auto reset to "Ready" after 4 seconds
    setTimeout(() => {
        if (el.textContent === msg) {
            el.textContent = "Ready";
        }
    }, 4000);
}

// // Safe stringify helper
function jsonStringify(obj) {
    return JSON.stringify(obj);
}

// --- Publish Button Disabling Helper ---
function updatePublishButtonState() {
    const body = document.getElementById("composer-body").value;
    const btn = document.getElementById("btn-publish");
    btn.disabled = (!body || !body.trim());
}
