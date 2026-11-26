// TierForge AI Bridge — Content Script Adapter (Gemini Web, ChatGPT, Claude Web)
if (!window.__TIERFORGE_BRIDGE_LOADED__) {
  window.__TIERFORGE_BRIDGE_LOADED__ = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
      if (btn && !btn.disabled) return btn;
    }
    return null;
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
    const input = findInputEl(service);
    if (!input) {
      return { ok: false, error: `Input box not found on ${service} tab (make sure you are logged in).` };
    }

    const beforeText = readLatestResponseText(service);
    input.focus();
    if (input.tagName === "TEXTAREA" || input.tagName === "INPUT") {
      input.value = prompt;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      input.innerText = prompt;
      input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: prompt }));
    }

    await sleep(300);
    const btn = findSendButton(service);
    if (btn) {
      btn.click();
    } else {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
    }

    // Wait for response text to appear and stabilize for 2.5s (up to 90s)
    const deadline = Date.now() + 90000;
    let lastText = beforeText;
    let stableCount = 0;

    while (Date.now() < deadline) {
      await sleep(800);
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
    return { ok: false, error: "Timed out waiting for Web AI response." };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "TIERFORGE_RUN_PROMPT") {
      runPromptInDom(msg.service || "gemini", msg.prompt || "").then(sendResponse);
      return true;
    }
    return false;
  });
}
