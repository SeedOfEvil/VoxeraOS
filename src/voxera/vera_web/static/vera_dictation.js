// Vera dictation enhancer — bounded browser-mic capture for canonical
// Vera.  The mic button and voice bar stay hidden until this script
// confirms that the runtime has voice input enabled AND the browser
// supports MediaRecorder + getUserMedia.  There is no always-on
// listening: recording starts only on an explicit click of the mic
// button, stops on a second click, and the captured blob is POSTed
// to /chat/voice/stream (the canonical STT -> run_vera_chat_turn ->
// progressive text-chunk + early-chunk TTS path).  A legacy batch
// fallback to /chat/voice is kept for browsers without streaming
// ReadableStream support or when the stream fetch fails before any
// event is seen.
//
// Rendering parity: the /chat/voice/stream ``done`` event carries the
// canonical turns array produced by the shared chat helper.  We hand
// that array to the main page IIFE's ``window.__veraApplyServerTurns``
// hook so assistant replies render through the SAME bounded markdown
// subset that typed replies use.  There is no second renderer to
// drift.  Until the ``done`` event arrives, chunk text renders into a
// progressive "is-streaming" bubble using escape-only plain text so
// partial markdown (unterminated fenced block, etc.) cannot produce
// flicker artifacts.
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
    setState("Requesting microphone…");
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
        setState("Recording… click again to send");
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
    setState("Stopping…");
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
      setError("No audio was captured — please try again.");
      return;
    }
    var mime =
      (recorder && recorder.mimeType) ||
      (chunks[0] && chunks[0].type) ||
      "audio/webm";
    var blob = new Blob(chunks, { type: mime });
    chunks = [];
    setState("Uploading — " + Math.round(blob.size / 1024) + " KB…");
    uploadBlob(blob, mime);
  }

  // Track and cancel the staged "Transcribing…" / "Vera thinking…" /
  // "Synthesizing speech…" timers between states so a slow or fast
  // response does not leave the voice bar showing a stale stage.  The
  // bounded three-timer model is deliberate: we never fabricate
  // progress beyond what we know is true (upload done -> STT -> Vera
  // -> optional TTS).  "Synthesizing speech…" only fires when the
  // operator has asked for a spoken reply AND enough wall time has
  // elapsed that Vera has almost certainly produced text.  If any
  // timer's guard condition (``uploading``) is no longer true when
  // it fires, it becomes a no-op so the label never drifts past the
  // real pipeline state.
  var _stagingTimer1 = null;
  var _stagingTimer2 = null;
  var _stagingTimer3 = null;

  function clearStagingTimers() {
    if (_stagingTimer1) {
      clearTimeout(_stagingTimer1);
      _stagingTimer1 = null;
    }
    if (_stagingTimer2) {
      clearTimeout(_stagingTimer2);
      _stagingTimer2 = null;
    }
    if (_stagingTimer3) {
      clearTimeout(_stagingTimer3);
      _stagingTimer3 = null;
    }
  }

  function uploadBlob(blob, mime) {
    uploading = true;
    micBtn.disabled = true;
    var speakResponse =
      speakCheckbox && !speakCheckbox.disabled && speakCheckbox.checked;

    // Progressive state transitions while the request is in flight.
    // Each timer is cleared the moment the response arrives so the
    // final state always reflects truthful server-reported progress,
    // never a fabricated or lingering in-flight label.
    clearStagingTimers();
    _stagingTimer1 = setTimeout(function () {
      if (uploading) setState("Transcribing…");
    }, 350);
    _stagingTimer2 = setTimeout(function () {
      if (uploading) setState("Vera thinking…");
    }, 1400);
    if (speakResponse) {
      _stagingTimer3 = setTimeout(function () {
        if (uploading) setState("Synthesizing speech…");
      }, 4500);
    }

    // Prefer the streaming endpoint so the reply renders
    // progressively and TTS starts from the first stable chunk.
    // Falls back to the legacy batch endpoint if the browser lacks
    // ReadableStream reader support or the stream fetch fails before
    // producing any events.  Both endpoints share the same canonical
    // STT / Vera / preview trust boundaries server-side.
    var canStream =
      typeof window.ReadableStream === "function" &&
      typeof TextDecoder === "function";
    if (canStream) {
      streamDictation(blob, mime, speakResponse).catch(function (err) {
        // streamDictation only rejects when the initial fetch failed
        // before any event arrived (network / 4xx).  Fall back to the
        // legacy endpoint so one stream failure does not drop the
        // operator's turn.  Mid-stream errors are handled inline and
        // never reach this catch.
        setError(errMessage(err) || "Streaming unavailable — retrying batch mode.");
        postBatchDictation(blob, mime, speakResponse);
      });
    } else {
      postBatchDictation(blob, mime, speakResponse);
    }
  }

  // Batch fallback — the legacy ``/chat/voice`` JSON round-trip.
  // Kept intact so operators with older browsers (no ReadableStream)
  // and any direct client still see the same trust-boundary-
  // preserving behavior.  Also the safety net when the streaming
  // endpoint fails before any event is emitted.
  function postBatchDictation(blob, mime, speakResponse) {
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
        clearStagingTimers();
        micBtn.disabled = false;
        var payload = result.payload || {};
        if (!result.status || result.status >= 400 || payload.ok === false) {
          var msg =
            payload.error ||
            payload.detail ||
            ("Dictation failed (" + result.status + ")");
          setState("Idle");
          setError(msg);
          if (payload && Array.isArray(payload.turns)) {
            applyTurnsUpdate(payload.turns, payload.turn_count);
          }
          if (payload && payload.has_preview_truth === true) {
            applyPreviewUpdate(payload.preview, payload.session_id);
          }
          return;
        }
        applyDictationResult(payload);
      })
      .catch(function (err) {
        uploading = false;
        clearStagingTimers();
        micBtn.disabled = false;
        setState("Idle");
        setError(errMessage(err));
      });
  }

  // Streaming dictation — progressive text + ordered audio.
  //
  // Reads an NDJSON stream from ``/chat/voice/stream``.  Each line is
  // a single event (see ``vera_web.app._run_voice_stream`` for the
  // schema).  The client:
  //
  //   * inserts the user's voice-transcript bubble as soon as the
  //     ``stt`` event lands, BEFORE any assistant progressive bubble
  //     appears, so chat ordering never shows Vera replying to a
  //     transcript the operator cannot see;
  //   * renders ``text_chunk`` events into a growing assistant
  //     bubble appended AFTER the user bubble;
  //   * queues ``audio_chunk`` URLs into an in-order playback queue
  //     that starts the moment the first chunk lands;
  //   * swaps the progressive bubble + placeholder user bubble for
  //     the canonical rendered turns on ``done`` so markdown lands
  //     identically to typed replies.
  //
  // Fails soft: a broken stream surfaces an error line and the
  // partial rendered text stays on screen.  An init-time failure
  // rejects the returned promise so ``uploadBlob`` can fall back to
  // the batch endpoint without dropping the operator's turn.
  function streamDictation(blob, mime, speakResponse) {
    var params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (speakResponse) params.set("speak_response", "1");
    var url = "/chat/voice/stream";
    var qs = params.toString();
    if (qs) url += "?" + qs;

    // Progressive assistant bubble is NOT created up front.  It is
    // created lazily on the first text_chunk event, AFTER the user
    // transcript bubble has been inserted on the stt event.  This
    // guarantees correct conversation ordering on screen: user
    // turn first, then Vera's reply.
    var progressive = null;
    var userTranscriptRendered = false;
    var audioQueue = [];
    var playing = false;
    var firstAudioPlayed = false;
    var anyEventSeen = false;
    var finalPayload = null;

    function ensureProgressiveBubble() {
      if (progressive === null) {
        progressive = beginProgressiveAssistantBubble();
      }
      return progressive;
    }

    function playNext() {
      if (playing) return;
      var next = audioQueue.shift();
      if (!next || !audioEl) return;
      playing = true;
      audioEl.hidden = false;
      audioEl.src = next;
      if (!firstAudioPlayed) {
        firstAudioPlayed = true;
        setState("Speaking reply…");
      }
      var onEnd = function () {
        playing = false;
        audioEl.removeEventListener("ended", onEnd);
        audioEl.removeEventListener("error", onErr);
        playNext();
      };
      var onErr = function () {
        playing = false;
        audioEl.removeEventListener("ended", onEnd);
        audioEl.removeEventListener("error", onErr);
        playNext();
      };
      audioEl.addEventListener("ended", onEnd);
      audioEl.addEventListener("error", onErr);
      var playPromise = audioEl.play();
      if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(function () {
          playing = false;
          audioEl.removeEventListener("ended", onEnd);
          audioEl.removeEventListener("error", onErr);
          playNext();
        });
      }
    }

    function enqueueAudio(audioUrl) {
      audioQueue.push(audioUrl);
      playNext();
    }

    return new Promise(function (resolve, reject) {
      fetch(url, {
        method: "POST",
        body: blob,
        headers: { "Content-Type": mime || "audio/webm" },
        credentials: "same-origin",
      })
        .then(function (resp) {
          if (!resp.ok || !resp.body || !resp.body.getReader) {
            if (anyEventSeen) {
              resolve();
            } else {
              reject(new Error("Stream unavailable (" + resp.status + ")"));
            }
            return;
          }
          var reader = resp.body.getReader();
          var decoder = new TextDecoder("utf-8");
          var buffer = "";

          function pump() {
            reader
              .read()
              .then(function (result) {
                if (result.done) {
                  if (buffer.trim().length > 0) {
                    handleEventLine(buffer);
                    buffer = "";
                  }
                  finishStream();
                  return;
                }
                buffer += decoder.decode(result.value, { stream: true });
                var newlineIndex = buffer.indexOf("\n");
                while (newlineIndex !== -1) {
                  var line = buffer.slice(0, newlineIndex);
                  buffer = buffer.slice(newlineIndex + 1);
                  if (line.trim().length > 0) handleEventLine(line);
                  newlineIndex = buffer.indexOf("\n");
                }
                pump();
              })
              .catch(function (err) {
                setError(errMessage(err));
                finishStream();
              });
          }

          function handleEventLine(line) {
            anyEventSeen = true;
            var evt;
            try {
              evt = JSON.parse(line);
            } catch (_e) {
              return;
            }
            if (!evt || typeof evt.event !== "string") return;
            if (evt.event === "ready") return;
            if (evt.event === "stt") {
              // Render the user transcript bubble FIRST so the
              // thread never shows Vera replying before the
              // operator can see their own turn.  Only renders on
              // STT success; on STT failure the stream will emit a
              // terminal ``done`` with ok=false and no chunks.
              var stt = evt.stt;
              if (
                stt &&
                stt.success &&
                typeof stt.transcript === "string" &&
                stt.transcript.length > 0
              ) {
                appendUserTranscriptBubble(stt.transcript);
                userTranscriptRendered = true;
              }
              return;
            }
            if (evt.event === "reply_start") {
              setState("Vera replying…");
              return;
            }
            if (evt.event === "text_chunk") {
              // Lazy-create the progressive bubble so it always sits
              // AFTER any user transcript bubble we inserted on the
              // stt event.  If the stt event never rendered a user
              // bubble (unusual edge case), the progressive bubble
              // still lands at the end of the thread.
              ensureProgressiveBubble().appendChunk(String(evt.text || ""));
              return;
            }
            if (evt.event === "audio_chunk") {
              var audioUrl = String(evt.audio_url || "");
              if (audioUrl) enqueueAudio(audioUrl);
              return;
            }
            if (evt.event === "audio_chunk_failed") {
              return;
            }
            if (evt.event === "done") {
              finalPayload = evt;
            }
          }

          function finishStream() {
            uploading = false;
            clearStagingTimers();
            micBtn.disabled = false;
            if (finalPayload) {
              applyStreamingDone(finalPayload, firstAudioPlayed);
            } else {
              setState("Idle");
              setError("Stream ended unexpectedly.");
              if (progressive) progressive.finalize();
            }
            resolve();
          }

          pump();
        })
        .catch(function (err) {
          if (anyEventSeen) {
            resolve();
          } else {
            reject(err);
          }
        });
    });
  }

  // Insert the user's voice-transcript bubble into the thread.  This
  // is called the moment the ``stt`` event arrives so the user's
  // turn is visible BEFORE Vera's progressive reply bubble appears.
  // The canonical ``done`` event's ``turns`` array replaces this
  // placeholder with the canonical server-rendered user turn, so any
  // whitespace / normalisation differences are corrected at the end
  // of the stream.
  function appendUserTranscriptBubble(transcript) {
    var thread = document.getElementById("thread");
    if (!thread) return;
    var bubble = document.createElement("article");
    bubble.className = "bubble user is-streaming";
    bubble.dataset.streaming = "1";
    var role = document.createElement("div");
    role.className = "role";
    role.textContent = "You (voice transcript)";
    var textDiv = document.createElement("div");
    textDiv.className = "text";
    textDiv.textContent = String(transcript);
    bubble.appendChild(role);
    bubble.appendChild(textDiv);
    thread.appendChild(bubble);
    thread.scrollTop = thread.scrollHeight;
  }

  // Snapshot-apply the canonical ``done`` payload over the progressive
  // bubble so the final state matches the batch endpoint's rendered
  // output (bounded markdown, preview pane refresh, status-derived
  // state label).
  function applyStreamingDone(done, firstAudioPlayed) {
    if (!done) return;
    if (Array.isArray(done.turns)) {
      applyTurnsUpdate(done.turns, done.turns.length);
    }
    if (done.has_preview_truth === true) {
      applyPreviewUpdate(done.preview, done.session_id);
    }
    var sttOk = done.stt && done.stt.success;
    if (!sttOk) {
      var sttErr =
        (done.stt && done.stt.error) ||
        done.error ||
        "Transcription failed.";
      setState("Idle");
      setError(sttErr);
      return;
    }
    clearError();
    if (!firstAudioPlayed) {
      setState(deriveStateFromCanonicalStatus(done));
    }
  }

  // Progressive assistant bubble — a single dedicated thread entry
  // we grow as ``text_chunk`` events arrive.  On ``done`` it is
  // replaced wholesale by the canonical rendered turns so markdown
  // parity is preserved.  Until then the growing text uses plain
  // escape-only rendering to keep things legible without risking
  // partial-markdown artifacts.
  function beginProgressiveAssistantBubble() {
    var thread = document.getElementById("thread");
    if (!thread) {
      return { appendChunk: function () {}, finalize: function () {} };
    }
    var bubble = document.createElement("article");
    bubble.className = "bubble assistant is-streaming";
    bubble.dataset.streaming = "1";
    var role = document.createElement("div");
    role.className = "role";
    role.textContent = "Vera";
    var textDiv = document.createElement("div");
    textDiv.className = "text";
    bubble.appendChild(role);
    bubble.appendChild(textDiv);
    thread.appendChild(bubble);
    thread.scrollTop = thread.scrollHeight;
    var accumulated = "";
    return {
      appendChunk: function (chunkText) {
        if (!chunkText) return;
        if (accumulated.length > 0) accumulated += " ";
        accumulated += chunkText;
        textDiv.textContent = accumulated;
        thread.scrollTop = thread.scrollHeight;
      },
      finalize: function () {
        bubble.dataset.streaming = "0";
        bubble.classList.remove("is-streaming");
      },
    };
  }

  function applyDictationResult(payload) {
    // Render the thread and preview pane BEFORE touching state / TTS
    // so the text reply lands on the screen as soon as the server
    // returns.  TTS playback is strictly additive: if it fails or is
    // slow, the operator has already seen Vera's answer.
    if (payload && Array.isArray(payload.turns)) {
      applyTurnsUpdate(payload.turns, payload.turn_count);
    }
    if (payload && payload.has_preview_truth === true) {
      applyPreviewUpdate(payload.preview, payload.session_id);
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
    setState(deriveStateFromCanonicalStatus(payload));
    if (payload.tts_url && audioEl) {
      audioEl.hidden = false;
      audioEl.src = payload.tts_url;
      setState("Speaking reply…");
      var resetState = function () {
        setState(deriveStateFromCanonicalStatus(payload));
      };
      audioEl.onended = resetState;
      audioEl.onerror = resetState;
      var playPromise = audioEl.play();
      if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(function () {
          resetState();
        });
      }
    } else if (audioEl) {
      audioEl.hidden = true;
      audioEl.removeAttribute("src");
    }
  }

  // Derive an operator-facing state line from the canonical chat
  // status returned by /chat/voice.  The strings intentionally match
  // the dictation UX (concise, fits on the voice bar) rather than the
  // typed /chat status chip.
  function deriveStateFromCanonicalStatus(payload) {
    var status = String((payload && payload.status) || "").toLowerCase();
    if (!status) return "Idle";
    if (
      status === "handoff_submitted" ||
      status === "automation_definition_saved" ||
      status.indexOf("submitted") !== -1
    ) {
      return "Preview submitted to VoxeraOS.";
    }
    if (status === "blocked_path") {
      return "Request blocked (outside bounded paths).";
    }
    if (status === "voice_input_disabled" || status === "voice_input_invalid") {
      return "Voice input rejected by runtime.";
    }
    if (payload && payload.preview) {
      return "Preview drafted — review below.";
    }
    return "Idle";
  }

  function applyPreviewUpdate(preview, targetSessionId) {
    if (typeof window.__veraApplyServerPreview !== "function") return;
    try {
      window.__veraApplyServerPreview(preview, targetSessionId);
    } catch (_e) {
      // best-effort preview refresh only
    }
  }

  function applyTurnsUpdate(turns, turnCount) {
    if (typeof window.__veraApplyServerTurns === "function") {
      try {
        window.__veraApplyServerTurns(turns, turnCount);
        return;
      } catch (e) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn(
            "vera_dictation: __veraApplyServerTurns threw, " +
              "falling back to escape-only renderer",
            e,
          );
        }
      }
    } else if (typeof console !== "undefined" && console.warn) {
      console.warn(
        "vera_dictation: __veraApplyServerTurns missing, " +
          "falling back to escape-only renderer",
      );
    }
    var thread = document.getElementById("thread");
    if (!thread) return;
    var html = turns
      .map(function (turn) {
        var role = String(turn.role || "assistant");
        var roleLabel =
          role === "user" ? "You" : role === "assistant" ? "Vera" : role;
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
    thread.dataset.turnCount = String(
      Number.isFinite(Number(turnCount)) ? Number(turnCount) : turns.length,
    );
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
