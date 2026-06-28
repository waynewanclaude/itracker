// WebApp Application Logic

let activeThreadId = null;
let currentDraftId = null;
let config = {};

// On startup
document.addEventListener("DOMContentLoaded", async () => {
    await loadConfig();
    await refreshAll();
    
    if (config.use_fs_events) {
        console.log("Filesystem events enabled. Hooking up EventSource to /api/events");
        const eventSource = new EventSource("/api/events");
        eventSource.onmessage = async (event) => {
            if (event.data === "refresh") {
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
    
    // Bind UI actions
    document.getElementById("btn-new-thread").addEventListener("click", openNewThreadModal);
    document.getElementById("btn-close-modal").addEventListener("click", closeNewThreadModal);
    document.getElementById("btn-submit-thread").addEventListener("click", submitNewThread);
    document.getElementById("btn-new-user").addEventListener("click", openNewUserModal);
    document.getElementById("btn-close-user-modal").addEventListener("click", closeNewUserModal);
    document.getElementById("btn-submit-user").addEventListener("click", submitNewUser);
    document.getElementById("btn-scan").addEventListener("click", runCoordinatorScan);
    document.getElementById("btn-add-attachment").addEventListener("click", () => {
        document.getElementById("composer-file-input").click();
    });
    document.getElementById("composer-file-input").addEventListener("change", handleFileUpload);
    document.getElementById("btn-save-draft").addEventListener("click", saveDraft);
    document.getElementById("btn-publish").addEventListener("click", publishMessage);
    document.getElementById("btn-mark-done").addEventListener("click", markThreadDone);
    
    // Enter to publish, Shift+Enter to newline
    document.getElementById("composer-body").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            publishMessage();
        }
    });
});

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
    } catch (e) {
        console.error("Failed to load config", e);
    }
}

async function refreshAll() {
    await loadThreads();
    await loadUsers();
    await loadPending();
}

async function pollUpdates() {
    if (activeThreadId) {
        await loadThreadMessages(activeThreadId);
    }
    await loadUsers();
    await loadPending();
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
            item.textContent = `${t.title} (${t.thread_id})`;
            item.addEventListener("click", () => selectThread(t.thread_id, t.title, t.description_md || "", t.status));
            container.appendChild(item);
        });
    } catch (e) {
        console.error("Failed to load threads", e);
    }
}

function selectThread(threadId, title, desc, status) {
    activeThreadId = threadId;
    
    // Highlight active in list
    document.querySelectorAll(".thread-item").forEach(item => {
        if (item.textContent.includes(threadId)) {
            item.classList.add("active");
        } else {
            item.classList.remove("active");
        }
    });
    
    // Show header info
    document.getElementById("active-thread-title").textContent = title;
    document.getElementById("active-thread-desc").textContent = desc || "No description provided";
    
    const markDoneBtn = document.getElementById("btn-mark-done");
    if (status === "OPEN") {
        markDoneBtn.style.display = "block";
    } else {
        markDoneBtn.style.display = "none";
    }
    
    // Load messages
    loadThreadMessages(threadId);
    
    // Load or initialize draft
    setupDraftForThread(threadId);
}

async function loadThreadMessages(threadId) {
    try {
        const res = await fetch(`/api/threads/${threadId}/messages`);
        const messages = await res.json();
        const container = document.getElementById("message-timeline");
        container.innerHTML = "";
        
        if (messages.length === 0) {
            container.innerHTML = '<div class="empty-state">No messages in this thread yet.</div>';
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
        
        // Auto scroll to bottom
        container.scrollTop = container.scrollHeight;
    } catch (e) {
        console.error("Failed to load thread messages", e);
    }
}

async function markThreadDone() {
    if (!activeThreadId) return;
    if (!confirm("Are you sure you want to mark this thread as DONE? It will be closed and archived by the coordinator.")) return;
    
    try {
        const res = await fetch(`/api/threads/${activeThreadId}/done`, { method: "POST" });
        if (res.ok) {
            alert("Thread status updated to DONE.");
            await loadThreads();
            selectThread(activeThreadId, "", "", "DONE");
        } else {
            const err = await res.json();
            alert(`Error: ${err.error}`);
        }
    } catch (e) {
        console.error("Failed to mark thread done", e);
    }
}

// --- New Thread Modal ---

function openNewThreadModal() {
    document.getElementById("new-thread-modal").style.display = "flex";
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
            selectThread(threadId, title, description, "OPEN");
        } else {
            const err = await res.json();
            alert(`Failed to create thread: ${err.error}`);
        }
    } catch (e) {
        console.error("Failed to submit new thread", e);
    }
}

// --- Draft & Composer Operations ---

async function setupDraftForThread(threadId) {
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

// Safe stringify helper
function jsonStringify(obj) {
    return JSON.stringify(obj);
}

// --- User Management UI ---

function openNewUserModal() {
    document.getElementById("new-user-modal").style.display = "flex";
}

function closeNewUserModal() {
    document.getElementById("new-user-modal").style.display = "none";
}

async function submitNewUser() {
    const userId = document.getElementById("user-id-input").value.trim();
    if (!userId) {
        alert("Please enter a User ID.");
        return;
    }
    try {
        const res = await fetch("/api/users", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: jsonStringify({ user_id: userId })
        });
        if (res.ok) {
            closeNewUserModal();
            document.getElementById("user-id-input").value = "";
            await loadUsers();
        } else {
            const err = await res.json();
            alert(`Failed to add user: ${err.error}`);
        }
    } catch (e) {
        console.error("Failed to add user", e);
    }
}

async function loadUsers() {
    try {
        const res = await fetch("/api/users");
        const users = await res.json();
        const container = document.getElementById("user-list");
        container.innerHTML = "";
        
        if (users.length === 0) {
            container.innerHTML = '<div class="empty-state" style="font-size:0.8rem; padding:10px;">No registered users</div>';
            return;
        }
        
        users.forEach(u => {
            const item = document.createElement("div");
            item.className = "thread-item done"; // Reuses thread item style (grayed out style)
            item.style.cursor = "default";
            item.textContent = u;
            container.appendChild(item);
        });
    } catch (e) {
        console.error("Failed to load users", e);
    }
}
