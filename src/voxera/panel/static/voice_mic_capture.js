// Voxera Voice Workbench — browser microphone capture enhancer.
//
// Bounded, operator-initiated capture only: the UI block stays hidden
// until this script confirms the browser has MediaRecorder +
// getUserMedia. Recording starts only after an explicit "Start
// recording" click, stops only on "Stop & upload", and the captured
// blob is POSTed to /voice/workbench/mic-upload — the same route that
// feeds the canonical STT -> Vera -> optional TTS pipeline as the
// file-path form above. No always-on listening, no streaming, no
// autoplay.
(function () {
  "use strict";

  var block = document.querySelector('[data-testid="voice-workbench-mic-capture"]');
  if (!block) {
    return;
  }
  var startBtn = document.getElementById("voice-mic-start");
  var stopBtn = document.getElementById("voice-mic-stop");
  var stateEl = document.getElementById("voice-mic-state");
  var errorEl = document.getElementById("voice-mic-error");
  if (!startBtn || !stopBtn || !stateEl || !errorEl) {
    return;
  }

  var hasMediaDevices =
    typeof navigator !== "undefined" &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function";
  var hasMediaRecorder = typeof window.MediaRecorder === "function";
  if (!hasMediaDevices || !hasMediaRecorder) {
    block.hidden = false;
    setError(
      "This browser does not support microphone capture (MediaRecorder / getUserMedia). The file-path form above still works."
    );
    startBtn.disabled = true;
    stopBtn.disabled = true;
    setState("Unsupported browser");
    return;
  }
  block.hidden = false;

  var recorder = null;
  var activeStream = null;
  var chunks = [];
  var recording = false;

  startBtn.addEventListener("click", function () {
    clearError();
    if (recording) {
      return;
    }
    setState("Requesting microphone permission\u2026");
    startBtn.disabled = true;
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then(function (stream) {
        activeStream = stream;
        chunks = [];
        try {
          recorder = new MediaRecorder(stream);
        } catch (err) {
          stopStream();
          startBtn.disabled = false;
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
          setError(
            "Recorder error: " +
              (ev && ev.error && ev.error.name ? ev.error.name : "unknown")
          );
        });
        try {
          recorder.start();
        } catch (err) {
          stopStream();
          startBtn.disabled = false;
          setState("Idle");
          setError("Could not start recorder: " + errMessage(err));
          return;
        }
        recording = true;
        stopBtn.disabled = false;
        startBtn.disabled = true;
        setState("Recording\u2026 click \u201cStop & upload\u201d when done");
      })
      .catch(function (err) {
        startBtn.disabled = false;
        setState("Idle");
        if (err && err.name === "NotAllowedError") {
          setError(
            "Microphone permission denied. Grant access in your browser and try again."
          );
        } else if (err && err.name === "NotFoundError") {
          setError("No microphone device was found on this system.");
        } else {
          setError("Could not access microphone: " + errMessage(err));
        }
      });
  });

  stopBtn.addEventListener("click", function () {
    if (!recording || !recorder) {
      return;
    }
    stopBtn.disabled = true;
    setState("Stopping\u2026");
    try {
      recorder.stop();
    } catch (err) {
      setError("Could not stop recorder: " + errMessage(err));
      recording = false;
      stopStream();
      startBtn.disabled = false;
      setState("Idle");
    }
  });

  function handleRecorderStop() {
    recording = false;
    stopStream();
    if (!chunks.length) {
      startBtn.disabled = false;
      stopBtn.disabled = true;
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
    setState("Uploading \u2014 " + Math.round(blob.size / 1024) + " KB\u2026");
    uploadBlob(blob, mime);
  }

  function uploadBlob(blob, mime) {
    var form = document.querySelector('[data-testid="voice-workbench-form"]');
    var csrfToken = getFieldValue(form, 'input[name="csrf_token"]');
    var sessionId = getFieldValue(form, 'input[name="workbench_session_id"]');
    var language = getFieldValue(form, 'input[name="workbench_language"]');
    var sendToVera = isChecked(form, 'input[name="workbench_send_to_vera"]');
    var speakResponse = isChecked(form, 'input[name="workbench_speak_response"]');
    var params = new URLSearchParams();
    if (sessionId) params.set("workbench_session_id", sessionId);
    if (language) params.set("workbench_language", language);
    if (sendToVera) params.set("workbench_send_to_vera", "1");
    if (speakResponse) params.set("workbench_speak_response", "1");
    var url = "/voice/workbench/mic-upload";
    var qs = params.toString();
    if (qs) url += "?" + qs;
    var headers = { "Content-Type": mime || "audio/webm" };
    if (csrfToken) {
      headers["x-csrf-token"] = csrfToken;
    }
    fetch(url, {
      method: "POST",
      body: blob,
      headers: headers,
      credentials: "same-origin",
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.text().then(function (text) {
            throw new Error(
              "Upload failed (" + resp.status + "): " + (text || resp.statusText)
            );
          });
        }
        return resp.text();
      })
      .then(function (html) {
        // Replace the full document with the server-rendered workbench
        // result so the operator sees the same canonical result page
        // they would have seen from the file-path form submit.
        document.open();
        document.write(html);
        document.close();
      })
      .catch(function (err) {
        startBtn.disabled = false;
        stopBtn.disabled = true;
        setState("Idle");
        setError(errMessage(err));
      });
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
    if (recording) {
      block.classList.add("voice-mic-capture-active");
    } else {
      block.classList.remove("voice-mic-capture-active");
    }
  }

  function setError(text) {
    if (!errorEl) return;
    errorEl.textContent = text;
    errorEl.hidden = !text;
  }

  function clearError() {
    setError("");
  }

  function errMessage(err) {
    if (!err) return "unknown error";
    if (err.message) return err.message;
    if (err.name) return err.name;
    return String(err);
  }

  function getFieldValue(form, selector) {
    if (!form) return "";
    var el = form.querySelector(selector);
    if (!el) return "";
    return (el.value || "").trim();
  }

  function isChecked(form, selector) {
    if (!form) return false;
    var el = form.querySelector(selector);
    if (!el) return false;
    return !!el.checked;
  }
})();
