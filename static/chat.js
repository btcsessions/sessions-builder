const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatMessages = document.getElementById("chat-messages");
const clearBtn = document.getElementById("clear-chat");

/* --- Lightweight markdown → HTML --- */
function renderMarkdown(text) {
    // Escape HTML first
    let html = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    // Code blocks (``` ... ```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g,
        '<pre class="chat-code-block"><code>$2</code></pre>');

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code class="chat-inline-code">$1</code>');

    // Headers (### → h4, ## → h3, # → h2)
    html = html.replace(/^### (.+)$/gm, '<h4 class="chat-h">$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3 class="chat-h">$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2 class="chat-h">$1</h2>');

    // Bold **text** and __text__
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

    // Italic *text* and _text_
    html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');

    // Horizontal rules
    html = html.replace(/^---+$/gm, '<hr class="chat-hr">');

    // Unordered lists (- item)
    html = html.replace(/^(\s*)[-•] (.+)$/gm, '$1<li>$2</li>');
    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul class="chat-list">$1</ul>');

    // Numbered lists (1. item)
    html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
    // Wrap consecutive numbered <li> not already in <ul>
    html = html.replace(/(?<!<\/ul>)\n?((?:<li>.*<\/li>\n?)+)/g, function(match, items) {
        if (match.includes('<ul')) return match;
        return '<ol class="chat-list">' + items + '</ol>';
    });

    // Arrows → for visual clarity
    html = html.replace(/→/g, '<span class="chat-arrow">→</span>');

    // Paragraphs: double newlines
    html = html.replace(/\n\n+/g, '</p><p>');

    // Single newlines (that aren't inside block elements)
    html = html.replace(/(?<!<\/li>|<\/ul>|<\/ol>|<\/pre>|<\/h[234]>|<\/hr>)\n(?!<)/g, '<br>');

    // Wrap in paragraph
    html = '<p>' + html + '</p>';

    // Clean up empty paragraphs
    html = html.replace(/<p>\s*<\/p>/g, '');
    html = html.replace(/<p>\s*(<[huo])/g, '$1');
    html = html.replace(/(<\/[huo][l234]?>)\s*<\/p>/g, '$1');
    html = html.replace(/<p>\s*(<pre)/g, '$1');
    html = html.replace(/(<\/pre>)\s*<\/p>/g, '$1');
    html = html.replace(/<p>\s*(<hr)/g, '$1');

    return html;
}

/* --- Get current plan state from the DOM --- */
function getCurrentPlanState() {
    // Prefer the workspace's canonical reader (sees every field, including
    // description, section scripts, and live user edits)
    if (typeof window.plannerGetPlanState === "function") {
        return window.plannerGetPlanState();
    }
    const results = document.getElementById("plan-results");
    if (!results || results.style.display === "none") return null;

    const plan = {};

    // Titles
    const titleInputs = results.querySelectorAll(".title-editable");
    if (titleInputs.length) {
        plan.titles = [...titleInputs].map(el => el.value);
    }

    // Thumbnail ideas
    const thumbs = results.querySelectorAll("#plan-thumbnails .plan-option");
    if (thumbs.length) {
        plan.thumbnail_ideas = [...thumbs].map(el => el.textContent.trim().replace(/^\d+\s*/, ''));
    }

    // Hook
    const hook = document.getElementById("plan-hook");
    if (hook && hook.textContent.trim()) plan.intro_hook = hook.textContent.trim();

    // Outline
    const outlineSections = results.querySelectorAll(".outline-section");
    if (outlineSections.length) {
        plan.outline = [...outlineSections].map(sec => {
            const header = sec.querySelector(".outline-header strong");
            const points = [...sec.querySelectorAll("li")].map(li => li.textContent);
            return { section: header?.textContent || "", points };
        });
    }

    // Tags
    const tags = results.querySelectorAll("#plan-tags .tag");
    if (tags.length) plan.tags = [...tags].map(t => t.textContent);

    // Topic from form
    const topicEl = document.getElementById("video-topic");
    if (topicEl && topicEl.value) plan.topic = topicEl.value;

    // Format
    const formatEl = document.querySelector('input[name="video-type"]:checked');
    if (formatEl) plan.video_type = formatEl.value;

    return plan;
}

/* --- Append messages --- */
function appendMessage(role, content, planChange, lessonsSaved) {
    const welcome = chatMessages.querySelector(".chat-welcome");
    if (welcome) welcome.remove();

    const div = document.createElement("div");
    div.className = `chat-msg ${role}`;

    if (role === "assistant") {
        div.innerHTML = `<div class="msg-content">${renderMarkdown(content)}</div>`;
    } else {
        const d = document.createElement("div");
        d.textContent = content;
        div.innerHTML = `<div class="msg-content">${d.innerHTML}</div>`;
    }

    // If assistant proposes a plan change, add approve/reject buttons
    if (planChange && role === "assistant") {
        const changeBar = document.createElement("div");
        changeBar.className = "chat-plan-change";

        const label = document.createElement("span");
        label.className = "chat-change-label";
        label.textContent = "Suggested plan change:";

        const approveBtn = document.createElement("button");
        approveBtn.className = "btn small primary";
        approveBtn.textContent = "Apply";

        const rejectBtn = document.createElement("button");
        rejectBtn.className = "btn small";
        rejectBtn.textContent = "Dismiss";

        const status = document.createElement("span");
        status.className = "chat-change-status";

        approveBtn.addEventListener("click", () => {
            applyPlanChange(planChange);
            status.textContent = "Applied!";
            status.style.color = "var(--green, #4caf50)";
            approveBtn.remove();
            rejectBtn.remove();
        });

        rejectBtn.addEventListener("click", () => {
            status.textContent = "Dismissed";
            status.style.color = "var(--text-muted)";
            approveBtn.remove();
            rejectBtn.remove();
        });

        changeBar.appendChild(label);
        changeBar.appendChild(approveBtn);
        changeBar.appendChild(rejectBtn);
        changeBar.appendChild(status);
        div.appendChild(changeBar);
    }

    // If the assistant saved standing rules, confirm each with an undo
    if (lessonsSaved && lessonsSaved.length && role === "assistant") {
        lessonsSaved.forEach((lesson) => {
            const lessonBar = document.createElement("div");
            lessonBar.className = "chat-plan-change";

            const label = document.createElement("span");
            label.className = "chat-change-label";
            label.textContent = `📌 Saved for future plans: ${lesson.text}`;

            const undoBtn = document.createElement("button");
            undoBtn.className = "btn small";
            undoBtn.textContent = "Undo";

            const status = document.createElement("span");
            status.className = "chat-change-status";

            undoBtn.addEventListener("click", async () => {
                try {
                    const resp = await fetch("/api/lessons/remove", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ text: lesson.text }),
                    });
                    const data = await resp.json();
                    if (data.error) {
                        status.textContent = data.error;
                        status.style.color = "var(--text-muted)";
                    } else {
                        status.textContent = "Removed";
                        status.style.color = "var(--text-muted)";
                        undoBtn.remove();
                    }
                } catch (err) {
                    status.textContent = "Failed to remove";
                    status.style.color = "var(--text-muted)";
                }
            });

            lessonBar.appendChild(label);
            lessonBar.appendChild(undoBtn);
            lessonBar.appendChild(status);
            div.appendChild(lessonBar);
        });
    }

    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

/* --- Apply a plan change to the DOM --- */
function applyPlanChange(change) {
    // Prefer the workspace's canonical writer (merges into the real plan
    // state and re-renders the editable panel)
    if (typeof window.plannerApplyPlanChange === "function") {
        window.plannerApplyPlanChange(change);
        return;
    }
    if (change.titles) {
        if (typeof renderTitles === "function") {
            renderTitles(change.titles);
        }
    }
    if (change.thumbnail_ideas) {
        const el = document.getElementById("plan-thumbnails");
        if (el) {
            el.innerHTML = change.thumbnail_ideas.map((t, i) =>
                `<div class="plan-option"><span class="option-num">${i + 1}</span> ${escFn(t)}</div>`).join("");
        }
    }
    if (change.intro_hook) {
        const el = document.getElementById("plan-hook");
        if (el) el.textContent = change.intro_hook;
    }
    if (change.outline) {
        const el = document.getElementById("plan-outline");
        if (el) {
            el.innerHTML = change.outline.map(s => {
                const pts = s.points.map(p => `<li>${escFn(p)}</li>`).join("");
                return `<div class="outline-section"><div class="outline-header"><strong>${escFn(s.section)}</strong><span class="duration">${escFn(s.duration_hint || "")}</span></div><ul>${pts}</ul></div>`;
            }).join("");
        }
    }
    if (change.tags) {
        const el = document.getElementById("plan-tags");
        if (el) el.innerHTML = change.tags.map(t => `<span class="tag">${escFn(t)}</span>`).join("");
    }
    if (change.description) {
        const el = document.getElementById("plan-description");
        if (el) el.innerHTML = escFn(change.description).replace(/\n/g, "<br>");
    }
}

function escFn(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
}

/* --- Loading indicator --- */
function setLoading(on) {
    const existing = chatMessages.querySelector(".loading-indicator");
    if (on && !existing) {
        const div = document.createElement("div");
        div.className = "chat-msg assistant loading-indicator";
        div.innerHTML = '<div class="msg-content"><span class="chat-typing">Deep thinking...</span></div>';
        chatMessages.appendChild(div);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    } else if (!on && existing) {
        existing.remove();
    }
}

/* --- Submit handler --- */
chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;

    appendMessage("user", message);
    chatInput.value = "";
    chatInput.disabled = true;
    setLoading(true);

    try {
        const payload = { message };

        // Include current plan state so AI has context
        const planState = getCurrentPlanState();
        if (planState) payload.plan_state = planState;

        const resp = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();

        setLoading(false);

        if (data.error) {
            appendMessage("assistant", `Error: ${data.error}`);
        } else {
            appendMessage("assistant", data.reply, data.plan_change || null, data.lessons_saved || null);
        }
    } catch (err) {
        setLoading(false);
        appendMessage("assistant", `Error: ${err.message}`);
    }

    chatInput.disabled = false;
    chatInput.focus();
});

clearBtn.addEventListener("click", async () => {
    await fetch("/api/clear-chat", { method: "POST" });
    chatMessages.innerHTML = `
        <div class="chat-welcome">
            <p>Ask me anything — I can pull in links and live trends:</p>
            <ul>
                <li>"What topics get the most views?"</li>
                <li>"How can I capitalize on this? https://..."</li>
                <li>"What's trending right now that fits my niche?"</li>
                <li>"Rewrite the intro hook"</li>
            </ul>
        </div>`;
});
