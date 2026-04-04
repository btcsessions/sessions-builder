const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatMessages = document.getElementById("chat-messages");
const clearBtn = document.getElementById("clear-chat");

function appendMessage(role, content) {
    // Remove welcome message if present
    const welcome = chatMessages.querySelector(".chat-welcome");
    if (welcome) welcome.remove();

    const div = document.createElement("div");
    div.className = `chat-msg ${role}`;
    div.innerHTML = `<div class="msg-content">${escapeHtml(content)}</div>`;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function setLoading(on) {
    const existing = chatMessages.querySelector(".loading-indicator");
    if (on && !existing) {
        const div = document.createElement("div");
        div.className = "chat-msg assistant loading-indicator";
        div.innerHTML = '<div class="msg-content">Thinking...</div>';
        chatMessages.appendChild(div);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    } else if (!on && existing) {
        existing.remove();
    }
}

chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;

    appendMessage("user", message);
    chatInput.value = "";
    chatInput.disabled = true;
    setLoading(true);

    try {
        const resp = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
        });
        const data = await resp.json();

        setLoading(false);

        if (data.error) {
            appendMessage("assistant", `Error: ${data.error}`);
        } else {
            appendMessage("assistant", data.reply);
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
            <p>Ask me anything about your video performance!</p>
        </div>`;
});
