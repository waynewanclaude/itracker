// WebApp Application Logic

let activeThreadId = null;
let currentDraftId = null;
let config = {};
let lastMessageIds = [];
let selectedMessageIds = new Set();
let currentMessagesList = [];
let pollingPaused = false;
let lastUpdateTime = Date.now();

let stagedMessage = null; // { threadId, creator, messageId, folderName }
let activeThreadsList = [];
let archivedThreadsList = [];

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
    document.getElementById("btn-copy-selected").addEventListener("click", copySelectedMessages);
    document.getElementById("btn-unselect-all").addEventListener("click", () => {
        selectedMessageIds.clear();
        document.querySelectorAll(".message-card.selected").forEach(card => card.classList.remove("selected"));
        updateCopyButtonState();
    });
    document.getElementById("btn-clear-staged").addEventListener("click", clearStagedMessage);
    document.getElementById("btn-link-staged").addEventListener("click", linkStagedToSelected);
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
    const versionEl = document.getElementById("brand-version");
    if (versionEl) versionEl.style.color = secColor;
    const elapsedEl = document.getElementById("last-update-elapsed");
    if (elapsedEl) elapsedEl.style.color = secColor;
}

async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        config = await res.json();
        document.getElementById("sidebar-user-name").textContent = config.display_name || config.user_id;
        const identityText = config.role ? `${config.user_id}/${config.role}` : config.user_id;
        document.getElementById("user-badge").textContent = identityText;
        
        if (config.version) {
            document.getElementById("brand-version").textContent = `v${config.version}`;
        }
        
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
    const timeline = document.getElementById("message-timeline");
    pollingPaused = !pollingPaused;
    
    if (pollingPaused) {
        btn.innerHTML = '<strong>Pause</strong> <span class="transition-target">-&gt; Run</span>';
        if (timeline) {
            timeline.style.overflowY = "auto";
        }
        showScanStatus("Updates paused");
    } else {
        btn.innerHTML = '<strong>Run</strong> <span class="transition-target">-&gt; Pause</span>';
        if (timeline) {
            timeline.style.overflowY = "hidden";
        }
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
        activeThreadsList = threads;
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
        archivedThreadsList = threads;
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
    selectedMessageIds.clear(); // Clear selections when changing threads
    
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
    if (pauseBtn) {
        pauseBtn.style.display = "inline-block";
        if (pollingPaused) {
            pauseBtn.innerHTML = '<strong>Pause</strong> <span class="transition-target">-&gt; Run</span>';
            const timeline = document.getElementById("message-timeline");
            if (timeline) timeline.style.overflowY = "auto";
        } else {
            pauseBtn.innerHTML = '<strong>Run</strong> <span class="transition-target">-&gt; Pause</span>';
            const timeline = document.getElementById("message-timeline");
            if (timeline) timeline.style.overflowY = "hidden";
        }
    }
    const copyBtn = document.getElementById("btn-copy-selected");
    const unselectBtn = document.getElementById("btn-unselect-all");
    if (copyBtn) {
        copyBtn.style.display = "inline-block";
    }
    if (unselectBtn) {
        unselectBtn.style.display = "inline-block";
    }
    updateCopyButtonState();
    
    // Ownership checks for status controls (Lock, Archive)
    const currentUser = config.role ? `${config.user_id}/${config.role}` : config.user_id;
    const isOwner = (creatorUserId && creatorUserId === currentUser);
    
    const toggleLockBtn = document.getElementById("btn-toggle-lock");
    const archiveBtn = document.getElementById("btn-archive");
    
    if (status !== "ARCHIVED") {
        toggleLockBtn.style.display = "block";
        toggleLockBtn.disabled = !isOwner;
        
        if (isOwner) {
            if (status === "UNLOCK") {
                toggleLockBtn.innerHTML = '<strong>Unlock</strong> <span class="transition-target">-&gt; Restrict</span>';
                toggleLockBtn.setAttribute("data-next-status", "RESTRICT");
            } else if (status === "RESTRICT") {
                toggleLockBtn.innerHTML = '<strong>Restrict</strong> <span class="transition-target">-&gt; Lock</span>';
                toggleLockBtn.setAttribute("data-next-status", "LOCK");
            } else if (status === "LOCK") {
                toggleLockBtn.innerHTML = '<strong>Lock</strong> <span class="transition-target">-&gt; Unlock</span>';
                toggleLockBtn.setAttribute("data-next-status", "UNLOCK");
            }
        } else {
            // Non-owners see a static status indicator
            const capitalize = status.charAt(0) + status.slice(1).toLowerCase();
            toggleLockBtn.innerHTML = `<strong>${capitalize}</strong>`;
            toggleLockBtn.removeAttribute("data-next-status");
        }
        
        // Only show Archive button for owners
        archiveBtn.style.display = isOwner ? "block" : "none";
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
        currentMessagesList = messages;
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
            
            const messageId = `${msg.source_user_id}_${msg.source_local_message_id}_${msg.local_created_at}`;
            const isSelected = selectedMessageIds.has(messageId);
            
            const card = document.createElement("div");
            card.className = `message-card ${isSelf ? 'self' : ''} ${isSelected ? 'selected' : ''}`;
            card.dataset.msgCreator = msg.source_user_id;
            card.dataset.msgId = msg.source_local_message_id;
            
            card.addEventListener("click", () => {
                // If user is highlight-selecting text inside the message, skip toggling card selection
                const selection = window.getSelection().toString();
                if (selection) return;
                
                if (selectedMessageIds.has(messageId)) {
                    selectedMessageIds.delete(messageId);
                    card.classList.remove("selected");
                } else {
                    selectedMessageIds.add(messageId);
                    card.classList.add("selected");
                }
                updateCopyButtonState();
            });
            
            // Header
            const meta = document.createElement("div");
            meta.className = "message-meta";
            
            const timestamp = msg.local_created_at ? new Date(msg.local_created_at).toLocaleString() : "Unknown date";
            const indexInfo = msg.folder_name ? msg.folder_name.split("_")[1] : "";
            
            meta.innerHTML = `
                <span>
                    <strong>${msg.source_user_id}</strong> ${indexInfo ? `(#${parseInt(indexInfo)})` : ''}
                    <button class="btn-stage" title="Stage message for linking">🔗 Stage</button>
                </span>
                <span>${timestamp}</span>
            `;
            
            const stageBtn = meta.querySelector(".btn-stage");
            if (stageBtn) {
                stageBtn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    stageMessageForLinking(threadId, msg.source_user_id, msg.source_local_message_id, msg.folder_name);
                });
            }
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
                    
                    // Stop event propagation so clicking an attachment does not toggle card selection
                    tag.addEventListener("click", (e) => {
                        e.stopPropagation();
                    });
                    
                    attachDiv.appendChild(tag);
                });
                card.appendChild(attachDiv);
            }

            // Render spinoff/backlink list
            const spinoffs = msg.spinoff_links || [];
            const backlinks = msg.backlinks || [];
            if (spinoffs.length > 0 || backlinks.length > 0) {
                const linksContainer = document.createElement("div");
                linksContainer.className = "message-links-container";
                
                spinoffs.forEach(link => {
                    const badge = document.createElement("a");
                    badge.className = "link-badge spinoff-badge";
                    
                    let displayThread = link.target_thread_id;
                    if (displayThread.startsWith("T_")) displayThread = displayThread.slice(2).slice(0, 8);
                    
                    badge.innerHTML = `🔗 Spinoff: ${displayThread} (#${link.target_message_id})`;
                    badge.title = `Jump to spinoff message by ${link.target_message_creator}`;
                    
                    badge.addEventListener("click", (e) => {
                        e.stopPropagation();
                        jumpToMessage(link.target_thread_id, link.target_message_creator, link.target_message_id);
                    });
                    linksContainer.appendChild(badge);
                });
                
                backlinks.forEach(link => {
                    const badge = document.createElement("a");
                    badge.className = "link-badge backlink-badge";
                    
                    let displayThread = link.source_thread_id;
                    if (displayThread.startsWith("T_")) displayThread = displayThread.slice(2).slice(0, 8);
                    
                    badge.innerHTML = `↩️ Backlink: ${displayThread} (#${link.source_message_id})`;
                    badge.title = `Jump back to source message by ${link.source_message_creator}`;
                    
                    badge.addEventListener("click", (e) => {
                        e.stopPropagation();
                        jumpToMessage(link.source_thread_id, link.source_message_creator, link.source_message_id);
                    });
                    linksContainer.appendChild(badge);
                });
                
                card.appendChild(linksContainer);
            }
            
            container.appendChild(card);
        });
        
        // Auto scroll to bottom only when there are new messages
        if (hasNewMessages) {
            container.scrollTop = container.scrollHeight;
        }
        
        // Update cached IDs
        lastMessageIds = messageIds;
        updateCopyButtonState();
        recordUIUpdate();
    } catch (e) {
        console.error("Failed to load thread messages", e);
    }
}

async function cycleThreadStatus() {
    if (!activeThreadId) return;
    
    const btn = document.getElementById("btn-toggle-lock");
    const nextStatus = btn.getAttribute("data-next-status") || "UNLOCK";
    
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
            document.getElementById("btn-copy-selected").style.display = "none";
            document.getElementById("btn-unselect-all").style.display = "none";
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
    const bodyContainer = document.getElementById("composer-body-container");
    const composerArea = document.getElementById("composer-area");
    
    composerArea.style.display = "block";
    
    if (status === "LOCK") {
        bodyContainer.style.display = "none";
        noticeEl.textContent = "This thread is locked. No further messages can be posted.";
        noticeEl.style.display = "block";
        return;
    } else if (status === "ARCHIVED") {
        bodyContainer.style.display = "none";
        noticeEl.textContent = "This thread is archived and closed. No further messages can be posted.";
        noticeEl.style.display = "block";
        return;
    } else if (status === "RESTRICT" && !isOwner) {
        bodyContainer.style.display = "none";
        noticeEl.textContent = "This thread is restricted. Only the thread creator can post messages.";
        noticeEl.style.display = "block";
        return;
    }
    
    noticeEl.style.display = "none";
    bodyContainer.style.display = "flex";
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

// --- Clipboard Copy Helpers ---
function updateCopyButtonState() {
    const copyBtn = document.getElementById("btn-copy-selected");
    const unselectBtn = document.getElementById("btn-unselect-all");
    const hasSelections = (selectedMessageIds.size > 0);
    if (copyBtn) {
        copyBtn.disabled = !hasSelections;
    }
    if (unselectBtn) {
        unselectBtn.disabled = !hasSelections;
    }
    updateLinkButtonState();
}

async function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
    } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
    }
}

function copySelectedMessages() {
    if (selectedMessageIds.size === 0) return;
    
    // Sort selections chronologically based on their index in currentMessagesList
    const selectedMsgs = currentMessagesList.filter(msg => {
        const messageId = `${msg.source_user_id}_${msg.source_local_message_id}_${msg.local_created_at}`;
        return selectedMessageIds.has(messageId);
    });
    
    // Format each selected message
    const formattedBlocks = selectedMsgs.map(msg => {
        const timestamp = msg.local_created_at ? new Date(msg.local_created_at).toLocaleString() : "Unknown date";
        const writer = msg.source_user_id;
        const body = msg.body;
        return `[${timestamp}] ${writer}:\n${body}\n--------------------`;
    });
    
    const clipboardText = formattedBlocks.join("\n\n");
    
    copyToClipboard(clipboardText).then(() => {
        // Clear selections
        selectedMessageIds.clear();
        document.querySelectorAll(".message-card.selected").forEach(card => card.classList.remove("selected"));
        updateCopyButtonState();

        // Provide dynamic visual feedback "Copied!" for 2 seconds
        const copyBtn = document.getElementById("btn-copy-selected");
        const originalText = copyBtn.textContent;
        copyBtn.textContent = "Copied!";
        copyBtn.classList.add("btn-success");
        copyBtn.classList.remove("btn-secondary");
        setTimeout(() => {
            copyBtn.textContent = originalText;
            copyBtn.classList.remove("btn-success");
            copyBtn.classList.add("btn-secondary");
        }, 2000);
    }).catch(err => {
        console.error("Copy failed", err);
        alert("Failed to copy to clipboard.");
    });
}

// --- Staging Dock Core Logic ---
function stageMessageForLinking(threadId, creator, messageId, folderName) {
    stagedMessage = { threadId, creator, messageId, folderName };
    const bar = document.getElementById("staging-bar");
    const text = document.getElementById("staging-text");
    if (bar && text) {
        let displayThread = threadId;
        if (displayThread.startsWith("T_")) displayThread = displayThread.slice(2).slice(0, 8);
        text.textContent = `[${messageId}] by ${creator} (Thread: ${displayThread})`;
        bar.style.display = "flex";
    }
    updateLinkButtonState();
}

function clearStagedMessage() {
    stagedMessage = null;
    const bar = document.getElementById("staging-bar");
    if (bar) {
        bar.style.display = "none";
    }
}

function updateLinkButtonState() {
    const linkBtn = document.getElementById("btn-link-staged");
    if (linkBtn) {
        const hasSelection = (selectedMessageIds.size > 0);
        linkBtn.disabled = (!stagedMessage || !hasSelection);
    }
}

async function linkStagedToSelected() {
    if (!stagedMessage || selectedMessageIds.size === 0) return;
    
    const targetMsgIdStr = Array.from(selectedMessageIds)[0];
    const targetMsg = currentMessagesList.find(msg => {
        const id = `${msg.source_user_id}_${msg.source_local_message_id}_${msg.local_created_at}`;
        return id === targetMsgIdStr;
    });
    
    if (!targetMsg) {
        alert("Failed to find target message in active list.");
        return;
    }
    
    const linkBtn = document.getElementById("btn-link-staged");
    if (linkBtn) {
        linkBtn.disabled = true;
        linkBtn.textContent = "Linking...";
    }
    
    try {
        const res = await fetch("/api/commands", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: jsonStringify({
                command_type: "LINK_MESSAGES",
                params: {
                    source_thread_id: stagedMessage.threadId,
                    source_message_creator: stagedMessage.creator,
                    source_message_id: stagedMessage.messageId,
                    target_thread_id: activeThreadId,
                    target_message_creator: targetMsg.source_user_id,
                    target_message_id: targetMsg.source_local_message_id
                }
            })
        });
        
        if (res.ok) {
            showScanStatus("Messages linked successfully!");
            clearStagedMessage();
            
            // Clear selections
            selectedMessageIds.clear();
            document.querySelectorAll(".message-card.selected").forEach(card => card.classList.remove("selected"));
            updateCopyButtonState();
            
            await loadThreadMessages(activeThreadId);
        } else {
            const err = await res.json();
            alert(`Link failed: ${err.error}`);
        }
    } catch (e) {
        console.error("Link command failed", e);
        alert("Failed to send link command.");
    } finally {
        if (linkBtn) {
            linkBtn.textContent = "Link to Selected";
            updateLinkButtonState();
        }
    }
}

async function jumpToMessage(threadId, creator, messageId) {
    let thread = activeThreadsList.find(t => t.thread_id === threadId);
    let isArchived = false;
    
    if (!thread) {
        thread = archivedThreadsList.find(t => t.thread_id === threadId);
        isArchived = true;
    }
    
    if (thread) {
        const currentUser = config.role ? `${config.user_id}/${config.role}` : config.user_id;
        const status = isArchived ? "ARCHIVED" : (thread.status || "UNLOCK");
        selectThread(threadId, thread.title, thread.description_md || "", status, thread.hostname, thread.created_at, thread.creator_user_id);
    } else {
        selectThread(threadId, threadId, "", "UNLOCK", "", "", "");
    }
    
    // Smooth scroll and pulse highlight targeting after a brief render delay
    setTimeout(() => {
        const targetCard = document.querySelector(`.message-card[data-msg-creator="${creator}"][data-msg-id="${messageId}"]`);
        if (targetCard) {
            targetCard.scrollIntoView({ behavior: "smooth", block: "center" });
            targetCard.classList.add("highlight-pulse");
            setTimeout(() => {
                targetCard.classList.remove("highlight-pulse");
            }, 2500);
        }
    }, 500);
}

