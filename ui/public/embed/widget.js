/**
 * SPX Voice Universal Website Lead Capture & Call Widget
 * Version: 2.0.0
 */
(function() {
  'use strict';

  // If full voice-agent-widget is desired, load it
  const currentScript = document.currentScript || document.querySelector('script[src*="widget.js"]');
  const orgId = currentScript ? (currentScript.getAttribute('data-org-id') || '1') : '1';
  const apiEndpoint = currentScript ? (currentScript.getAttribute('data-api-endpoint') || window.location.origin) : window.location.origin;

  // Create floating trigger button
  function createWidget() {
    if (document.getElementById('spx-voice-widget-root')) return;

    const root = document.createElement('div');
    root.id = 'spx-voice-widget-root';
    root.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:999999;font-family:system-ui,-apple-system,sans-serif;';

    root.innerHTML = `
      <style>
        .spx-btn {
          background: linear-gradient(135deg, #2563eb, #4f46e5);
          color: white;
          border: none;
          border-radius: 9999px;
          padding: 12px 20px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          box-shadow: 0 4px 14px rgba(37,99,235,0.35);
          display: flex;
          align-items: center;
          gap: 8px;
          transition: transform 0.2s, box-shadow 0.2s;
        }
        .spx-btn:hover {
          transform: translateY(-2px);
          box-shadow: 0 6px 20px rgba(37,99,235,0.45);
        }
        .spx-modal {
          display: none;
          position: fixed;
          bottom: 80px;
          right: 24px;
          width: 320px;
          background: #ffffff;
          color: #0f172a;
          border-radius: 16px;
          box-shadow: 0 10px 30px rgba(0,0,0,0.18);
          border: 1px solid #e2e8f0;
          padding: 20px;
          box-sizing: border-box;
        }
        .spx-modal input {
          width: 100%;
          padding: 8px 12px;
          margin-bottom: 10px;
          border: 1px solid #cbd5e1;
          border-radius: 8px;
          font-size: 13px;
          box-sizing: border-box;
        }
        .spx-submit {
          width: 100%;
          background: #2563eb;
          color: white;
          border: none;
          padding: 10px;
          border-radius: 8px;
          font-weight: 600;
          font-size: 13px;
          cursor: pointer;
        }
      </style>
      <button class="spx-btn" id="spx-toggle-btn">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>
        Instant AI Call
      </button>
      <div class="spx-modal" id="spx-modal">
        <div style="font-weight:700;font-size:15px;margin-bottom:4px;">Request an AI Call</div>
        <div style="font-size:12px;color:#64748b;margin-bottom:14px;">Speak with our voice AI assistant in seconds.</div>
        <form id="spx-form">
          <input type="text" id="spx-name" placeholder="Your Name" required />
          <input type="tel" id="spx-phone" placeholder="Phone Number (e.g. +91...)" required />
          <button type="submit" class="spx-submit" id="spx-submit-btn">Call Me Now</button>
        </form>
        <div id="spx-status" style="font-size:12px;margin-top:8px;text-align:center;"></div>
      </div>
    `;

    document.body.appendChild(root);

    const toggleBtn = document.getElementById('spx-toggle-btn');
    const modal = document.getElementById('spx-modal');
    const form = document.getElementById('spx-form');
    const statusDiv = document.getElementById('spx-status');

    toggleBtn.addEventListener('click', function() {
      modal.style.display = modal.style.display === 'block' ? 'none' : 'block';
    });

    form.addEventListener('submit', async function(e) {
      e.preventDefault();
      const name = document.getElementById('spx-name').value;
      const phone = document.getElementById('spx-phone').value;
      const submitBtn = document.getElementById('spx-submit-btn');

      submitBtn.disabled = true;
      submitBtn.innerText = 'Connecting...';
      statusDiv.innerText = '';

      try {
        const res = await fetch(`${apiEndpoint}/api/v1/business/leads/capture`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: name,
            phone: phone,
            organization_id: parseInt(orgId, 10),
            source: 'website_embed_widget',
          }),
        });

        const data = await res.json();
        if (data.status === 'success') {
          statusDiv.style.color = '#16a34a';
          statusDiv.innerText = 'Call requested! Connecting to your phone.';
          setTimeout(function() { modal.style.display = 'none'; }, 3000);
        } else if (data.status === 'suppressed') {
          statusDiv.style.color = '#dc2626';
          statusDiv.innerText = 'Number is on Do-Not-Call registry.';
        } else {
          statusDiv.style.color = '#dc2626';
          statusDiv.innerText = data.message || 'Request failed. Try again.';
        }
      } catch (err) {
        statusDiv.style.color = '#dc2626';
        statusDiv.innerText = 'Network error. Please try again.';
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerText = 'Call Me Now';
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createWidget);
  } else {
    createWidget();
  }
})();

