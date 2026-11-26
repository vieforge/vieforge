// TierForge AI Bridge — Content Script Adapter (Gemini Web, ChatGPT, Claude Web)
if (!window.__TIERFORGE_BRIDGE_LOADED__) {
  window.__TIERFORGE_BRIDGE_LOADED__ = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function detectAuthOrCaptchaBlock() {
    const href = String(window.location.href || "").toLowerCase();
    const title = String(document.title || "").toLowerCase();
    const bodyHead = String((document.body && document.body.innerText) || "").slice(0, 1200).toLowerCase();

    if (
      href.includes("accounts.google.com") ||
      href.includes("/auth/login") ||
      href.includes("/login")
    ) {
      return "LOGIN_REQUIRED: Please sign in manually in this browser tab, then retry.";
    }
    if (
      href.includes("challenges.cloudflare.com") ||
      title.includes("just a moment") ||
      bodyHead.includes("verify you are human") ||
      bodyHead.includes("unusual traffic") ||
      Boolean(document.querySelector('iframe[src*="recaptcha"], iframe[src*="turnstile"], iframe[src*="hcaptcha"]'))
    ) {
      return "LOGIN_OR_CAPTCHA_REQUIRED: Human verification / CAPTCHA detected. Complete it manually in the browser tab.";
    }
    return "";
  }

  function findInputEl(service) {
    const selectors = {
      gemini: [
        'div.ql-editor[contenteditable="true"]',
        'rich-textarea div[contenteditable="true"]',
        'div[role="textbox"][contenteditable="true"]',
      ],
      chatgpt: [
        '#prompt-textarea',
        'div[contenteditable="true"][id*="prompt"]',
        'textarea',
      ],
      claude: [
        'div.ProseMirror[contenteditable="true"]',
        'div[contenteditable="true"]',
      ],
    };
    const list = selectors[service] || selectors.gemini;
    for (const sel of list) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  async function waitForInputEl(service, timeoutMs = 12000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const block = detectAuthOrCaptchaBlock();
      if (block) return { el: null, blockError: block };
      const el = findInputEl(service);
      if (el) return { el, blockError: "" };
      await sleep(400);
    }
    return { el: null, blockError: "" };
  }

  function findSendButton(service) {
    const selectors = {
      gemini: [
        'button.send-button',
        'button[aria-label*="Send"]',
        'button[aria-label*="Gửi"]',
      ],
      chatgpt: [
        'button[data-testid="send-button"]',
        'button[aria-label*="Send"]',
      ],
      claude: [
        'button[aria-label*="Send"]',
        'button[type="submit"]',
      ],
    };
    const list = selectors[service] || selectors.gemini;
    for (const sel of list) {
      const btn = document.querySelector(sel);
      if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") return btn;
    }
    return null;
  }

  function setEditorText(input, prompt) {
    input.focus();
    if (input.tagName === "TEXTAREA" || input.tagName === "INPUT") {
      input.value = prompt;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    // Native execCommand updates Quill (Gemini) and ProseMirror (ChatGPT/Claude) internal state reliably
    let inserted = false;
    try {
      document.execCommand("selectAll", false, null);
      inserted = document.execCommand("insertText", false, prompt);
    } catch (_err) {
      inserted = false;
    }
    if (!inserted || !String(input.innerText || "").trim()) {
      input.innerText = prompt;
      input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: prompt }));
    }
  }

  function readLatestResponseText(service) {
    const selectors = {
      gemini: ['model-response .markdown', 'message-content .markdown', 'model-response'],
      chatgpt: ['[data-message-author-role="assistant"] .markdown', '[data-message-author-role="assistant"]'],
      claude: ['.font-claude-message', '[data-is-streaming]'],
    };
    const list = selectors[service] || selectors.gemini;
    for (const sel of list) {
      const nodes = document.querySelectorAll(sel);
      if (nodes.length > 0) {
        return (nodes[nodes.length - 1].innerText || "").trim();
      }
    }
    return "";
  }

  async function runPromptInDom(service, prompt) {
    const { el: input, blockError } = await waitForInputEl(service, 12000);
    if (blockError) {
      return { ok: false, error: blockError };
    }
    if (!input) {
      return { ok: false, error: `Input box not found on ${service} tab after 12s (verify tab is open & signed in).` };
    }

    const beforeText = readLatestResponseText(service);
    setEditorText(input, prompt);

    // Wait up to 2.5s for Send button to enable after editor state sync
    let btn = null;
    const btnDeadline = Date.now() + 2500;
    while (Date.now() < btnDeadline) {
      btn = findSendButton(service);
      if (btn) break;
      await sleep(200);
    }
    if (btn) {
      btn.click();
    } else {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
    }

    // Wait for response text to appear and stabilize for ~2.4s (up to 90s)
    const deadline = Date.now() + 90000;
    let lastText = beforeText;
    let stableCount = 0;

    while (Date.now() < deadline) {
      await sleep(800);
      const midBlock = detectAuthOrCaptchaBlock();
      if (midBlock) {
        return { ok: false, error: midBlock };
      }
      const cur = readLatestResponseText(service);
      if (cur && cur !== beforeText) {
        if (cur === lastText) {
          stableCount += 1;
          if (stableCount >= 3) {
            return { ok: true, response_text: cur };
          }
        } else {
          lastText = cur;
          stableCount = 0;
        }
      }
    }

    if (lastText && lastText !== beforeText) {
      return { ok: true, response_text: lastText };
    }
    return { ok: false, error: "Timed out waiting for Web AI response (90s)." };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "TIERFORGE_RUN_PROMPT") {
      runPromptInDom(msg.service || "gemini", msg.prompt || "").then(sendResponse);
      return true;
    }
    return false;
  });
}
