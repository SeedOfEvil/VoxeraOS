// Vera dictation enhancer — bounded browser-mic capture for canonical
// Vera.  The mic button and voice bar stay hidden until this script
// confirms that the runtime has voice input enabled AND the browser
// supports MediaRecorder + getUserMedia.  There is no always-on
// listening: recording starts only on an explicit click of the mic
// button, stops on a second click, and the captured blob is POSTed
// to /chat/voice (the canonical STT -> Vera -> optional TTS path).
//
// Typed Vera still works even if JS or the mic is unavailable —
// progressive enhancement only.
(function () {
  "use strict";

  var micBtn = document.getElementById("vera-mic-btn");
  var voiceBar = document.getElementById("vera-voice-bar");
  if (!micBtn || !voiceBar) return;

  var stateEl = document.getElementById("vera-voice-state");
  var errorEl = document.getElementById("vera-voice-error");
  var audioEl = document.getElementById("vera-voice-audio");
  var speakCheckbox = document.getElementById("vera-voice-speak");
  var thread = document.getElementById("thread");
  var sessionInput = document.querySelector('input[name="session_id"]');
  var sessionId = sessionInput ? sessionInput.value : "";

  var voiceFoundationEnabled =
    voiceBar.dataset.voiceFoundationEnabled === "true";
  var voiceInputEnabled = voiceBar.dataset.voiceInputEnabled === "true";
  var voiceOutputEnabled = voiceBar.dataset.voiceOutputEnabled === "true";

  var hasMediaDevices =
    typeof navigator !== "undefined" &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function";
  var hasMediaRecorder = typeof window.MediaRecorder === "function";

  // Reveal the voice bar so the operator can see the truthful state
  // (enabled / disabled / unsupported) rather than silently hiding.
  voiceBar.hidden = false;

  if (!voiceFoundationEnabled || !voiceInputEnabled) {
    micBtn.hidden = true;
    setState("Voice input disabled in this runtime.");
    disableSpeakToggle(
      "Voice replies require voice output to be enabled in the runtime.",
    );
    return;
  }
  if (!hasMediaDevices || !hasMediaRecorder) {
    micBtn.hidden = true;
    setError(
      "This browser does not support microphone capture (MediaRecorder / getUserMedia). Typed chat still works.",
    );
    setState("Unsupported browser");
    disableSpeakToggle(null);
    return;
  }
  micBtn.hidden = false;
  if (!voiceOutputEnabled) {
    disableSpeakToggle(
      "Voice replies are disabled in this runtime (text still works).",
    );
  }

  var recorder = null;
  var activeStream = null;
  var chunks = [];
  var recording = false;
  var recorderErrored = false;
  var uploading = false;

  micBtn.addEventListener("click", function () {
    if (uploading) return;
    if (recording) {
      stopRecording();
    } else {
      startRecording();
    }
  });

  function startRecording() {
    clearError();
    setState("Requesting microphone\u2026");
    micBtn.disabled = true;
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then(function (stream) {
        activeStream = stream;
        chunks = [];
        recorderErrored = false;
        try {
          recorder = new MediaRecorder(stream);
        } catch (err) {
          stopStream();
          micBtn.disabled = false;
          setState("Idle");
          setError("Could not start recorder: " + errMessage(err));
          return;
        }
        recorder.addEventListener("dataavailable", function (ev) {
          if (ev && ev.data && ev.data.size > 0) {
            chunks.push(ev.data);
          }
        });
        recorder.addEventListener("stop", handleRecorderStop);
        recorder.addEventListener("error", function (ev) {
          recorderErrored = true;
          recording = false;
          chunks = [];
          stopStream();
          micBtn.disabled = false;
          micBtn.setAttribute("aria-pressed", "false");
          micBtn.classList.remove("is-recording");
          setState("Idle");
          setError(
            "Recorder error: " +
              (ev && ev.error && ev.error.name ? ev.error.name : "unknown"),
          );
        });
        try {
          recorder.start();
        } catch (err) {
          stopStream();
          micBtn.disabled = false;
          setState("Idle");
          setError("Could not start recorder: " + errMessage(err));
          return;
        }
        recording = true;
        micBtn.disabled = false;
        micBtn.setAttribute("aria-pressed", "true");
        micBtn.setAttribute("aria-label", "Stop dictation");
        micBtn.classList.add("is-recording");
        setState("Recording\u2026 click again to send");
      })
      .catch(function (err) {
        micBtn.disabled = false;
        setState("Idle");
        if (err && err.name === "NotAllowedError") {
          setError(
            "Microphone permission denied. Grant access in your browser and try again.",
          );
        } else if (err && err.name === "NotFoundError") {
          setError("No microphone device was found on this system.");
        } else {
          setError("Could not access microphone: " + errMessage(err));
        }
      });
  }

  function stopRecording() {
    if (!recording || !recorder) return;
    micBtn.disabled = true;
    setState("Stopping\u2026");
    try {
      recorder.stop();
    } catch (err) {
      setError("Could not stop recorder: " + errMessage(err));
      recording = false;
      stopStream();
      micBtn.disabled = false;
      micBtn.setAttribute("aria-pressed", "false");
      micBtn.classList.remove("is-recording");
      setState("Idle");
    }
  }

  function handleRecorderStop() {
    recording = false;
    stopStream();
    micBtn.setAttribute("aria-pressed", "false");
    micBtn.setAttribute("aria-label", "Start dictation");
    micBtn.classList.remove("is-recording");
    if (recorderErrored) {
      recorderErrored = false;
      chunks = [];
      micBtn.disabled = false;
      return;
    }
    if (!chunks.length) {
      micBtn.disabled = false;
      setState("Idle");
      setError("No audio was captured \u2014 please try again.");
      return;
    }
    var mime =
      (recorder && recorder.mimeType) ||
      (chunks[0] && chunks[0].type) ||
      "audio/webm";
    var blob = new Blob(chunks, { type: mime });
    chunks = [];
    setState(
      "Transcribing \u2014 " + Math.round(blob.size / 1024) + " KB\u2026",
    );
    uploadBlob(blob, mime);
  }

  function uploadBlob(blob, mime) {
    uploading = true;
    micBtn.disabled = true;
    var speakResponse =
      speakCheckbox && !speakCheckbox.disabled && speakCheckbox.checked;
    var params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (speakResponse) params.set("speak_response", "1");
    var url = "/chat/voice";
    var qs = params.toString();
    if (qs) url += "?" + qs;
    fetch(url, {
      method: "POST",
      body: blob,
      headers: { "Content-Type": mime || "audio/webm" },
      credentials: "same-origin",
    })
      .then(function (resp) {
        return resp
          .json()
          .catch(function () {
            return { ok: false, error: "Non-JSON response (" + resp.status + ")" };
          })
          .then(function (payload) {
            return { status: resp.status, payload: payload };
          });
      })
      .then(function (result) {
        uploading = false;
        micBtn.disabled = false;
        var payload = result.payload || {};
        if (!result.status || result.status >= 400 || payload.ok === false) {
          var msg = payload.error || ("Dictation failed (" + result.status + ")");
          setState("Idle");
          setError(msg);
          return;
        }
        applyDictationResult(payload);
      })
      .catch(function (err) {
        uploading = false;
        micBtn.disabled = false;
        setState("Idle");
        setError(errMessage(err));
      });
  }

  function applyDictationResult(payload) {
    if (payload && Array.isArray(payload.turns)) {
      renderTurns(payload.turns);
    }
    var sttOk = payload && payload.stt && payload.stt.success;
    if (!sttOk) {
      var sttErr =
        (payload && payload.stt && payload.stt.error) ||
        "Transcription failed.";
      setState("Idle");
      setError(sttErr);
      return;
    }
    clearError();
    var stateText = "Idle";
    if (payload.lifecycle && payload.lifecycle.ack) {
      stateText = payload.lifecycle.ok
        ? "Lifecycle action dispatched."
        : "Lifecycle action declined (canonical state unchanged).";
    } else if (payload.preview) {
      stateText = "Preview drafted \u2014 review below.";
    } else if (payload.show_action_guidance) {
      stateText = "Action-oriented request \u2014 continue in canonical Vera.";
    } else if (payload.vera && payload.vera.success) {
      stateText = "Idle";
    }
    setState(stateText);
    if (payload.tts_url && audioEl) {
      audioEl.hidden = false;
      audioEl.src = payload.tts_url;
      var playPromise = audioEl.play();
      if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(function () {
          // Autoplay may be blocked by the browser; the <audio>
          // element has controls, so the operator can press play.
        });
      }
    } else if (audioEl) {
      audioEl.hidden = true;
      audioEl.removeAttribute("src");
    }
  }

  function renderTurns(turns) {
    if (!thread) return;
    var html = turns
      .map(function (turn) {
        var role = String(turn.role || "assistant");
        var roleLabel = role === "user" ? "You" : role === "assistant" ? "Vera" : role;
        var origin = String(turn.input_origin || "");
        if (role === "user" && origin === "voice_transcript") {
          roleLabel = "You (voice transcript)";
        }
        var text = escapeHtml(String(turn.text || ""));
        return (
          '<article class="bubble ' +
          escapeHtml(role) +
          '"><div class="role">' +
          escapeHtml(roleLabel) +
          '</div><div class="text">' +
          text +
          "</div></article>"
        );
      })
      .join("");
    thread.innerHTML = html;
    thread.dataset.turnCount = String(turns.length);
    thread.scrollTop = thread.scrollHeight;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function stopStream() {
    if (activeStream) {
      try {
        activeStream.getTracks().forEach(function (track) {
          track.stop();
        });
      } catch (e) {
        // ignore — best-effort cleanup
      }
      activeStream = null;
    }
  }

  function setState(text) {
    if (stateEl) stateEl.textContent = text;
  }

  function setError(text) {
    if (!errorEl) return;
    errorEl.textContent = text || "";
    errorEl.hidden = !text;
  }

  function clearError() {
    setError("");
  }

  function disableSpeakToggle(reason) {
    if (!speakCheckbox) return;
    speakCheckbox.disabled = true;
    speakCheckbox.checked = false;
    var label = document.getElementById("vera-voice-toggle-label");
    if (label) {
      label.classList.add("is-disabled");
      if (reason) label.title = reason;
    }
  }

  function errMessage(err) {
    if (!err) return "unknown error";
    if (err.message) return err.message;
    if (err.name) return err.name;
    return String(err);
  }
})();
