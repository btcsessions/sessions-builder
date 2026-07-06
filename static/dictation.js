/* Voice dictation (Wispr Flow style): click the mic to record, click again
 * to stop. The audio goes to /api/transcribe (local Whisper server), the
 * transcript is cleaned up by the dictation-cleanup AI, and the refined
 * text auto-fills the target field.
 *
 * Manual toggle only — no silence detection, no hold-to-talk. Recording
 * runs until the user clicks Stop, so a long ramble is never cut off. */

function attachMic(btn, target, mode) {
    if (!btn || !target) return;

    // getUserMedia needs a secure context (https or localhost). The phone
    // path (http://<machine-ip>:5000) isn't one — disable with a hint.
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        btn.disabled = true;
        btn.title = "Microphone needs localhost or HTTPS — open the app at http://127.0.0.1:5000";
        return;
    }

    const idleLabel = btn.innerHTML;
    let recorder = null;
    let stream = null;
    let chunks = [];
    let busy = false;

    function setStatus(text, isError) {
        let el = btn.parentElement.querySelector(".mic-status");
        if (!el) {
            el = document.createElement("span");
            el.className = "mic-status";
            btn.insertAdjacentElement("afterend", el);
        }
        el.textContent = text || "";
        el.classList.toggle("error", !!isError);
    }

    function reset() {
        if (stream) stream.getTracks().forEach(t => t.stop());
        stream = null;
        recorder = null;
        chunks = [];
        busy = false;
        btn.classList.remove("recording", "refining");
        btn.innerHTML = idleLabel;
        btn.title = "Dictate";
    }

    async function start() {
        setStatus("");
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (err) {
            setStatus("Mic blocked — allow microphone access in the browser.", true);
            return;
        }
        chunks = [];
        recorder = new MediaRecorder(stream);
        recorder.addEventListener("dataavailable", (e) => {
            if (e.data && e.data.size) chunks.push(e.data);
        });
        recorder.addEventListener("stop", send);
        recorder.start();
        btn.classList.add("recording");
        btn.innerHTML = '<span class="mic-dot"></span> Stop';
        btn.title = "Click to stop and transcribe";
    }

    async function send() {
        const mime = (recorder && recorder.mimeType) || "audio/webm";
        const blob = new Blob(chunks, { type: mime });
        if (stream) stream.getTracks().forEach(t => t.stop());
        stream = null;

        if (!blob.size) { reset(); return; }

        busy = true;
        btn.classList.remove("recording");
        btn.classList.add("refining");
        btn.innerHTML = "Refining…";
        setStatus("");

        const ext = mime.includes("mp4") ? "mp4" : mime.includes("ogg") ? "ogg" : "webm";
        const form = new FormData();
        form.append("audio", blob, "dictation." + ext);
        form.append("mode", mode || "notes");

        try {
            // No Content-Type header — the browser sets the multipart boundary
            const resp = await fetch("/api/transcribe", { method: "POST", body: form });
            const data = await resp.json();
            if (data.error) {
                setStatus(data.error, true);
            } else if (!data.refined && !data.raw) {
                setStatus("No speech detected.", true);
            } else {
                target.value = data.refined || data.raw;
                target.dispatchEvent(new Event("input", { bubbles: true }));
                target.focus();
            }
        } catch (err) {
            setStatus("Transcription failed: " + err.message, true);
        }
        reset();
    }

    btn.addEventListener("click", (e) => {
        e.preventDefault();
        if (busy) return;  // refining — ignore clicks
        if (recorder && recorder.state === "recording") {
            recorder.stop();  // -> send()
        } else {
            start();
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("button[data-dictate]").forEach((btn) => {
        const target = document.getElementById(btn.dataset.dictate);
        attachMic(btn, target, btn.dataset.dictateMode || "notes");
    });
});
