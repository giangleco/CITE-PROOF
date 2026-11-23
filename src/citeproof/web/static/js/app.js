// CiteProof: cấu hình HTMX, CSRF cho request HTMX, toast, hộp thoại.
// HTMX vendored: htmx.org@2.0.11 dist/htmx.min.js,
// sha256 d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717
(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  var toastTimer = null;
  function showToast(message) {
    var el = document.getElementById("toast");
    if (!el || !message) return;
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2600);
  }

  function setup() {
    if (window.htmx) {
      htmx.config.allowEval = false;
      htmx.config.includeIndicatorStyles = false;
      htmx.config.allowScriptTags = false;
    }
    document.body.addEventListener("htmx:configRequest", function (evt) {
      evt.detail.headers["X-CSRF-Token"] = csrfToken();
    });
    document.body.addEventListener("toast", function (evt) {
      var detail = evt.detail || {};
      showToast(typeof detail === "string" ? detail : detail.value);
    });
    var initial = document.querySelector("[data-toast]");
    if (initial) showToast(initial.getAttribute("data-toast"));

    document.addEventListener("click", function (evt) {
      var opener = evt.target.closest("[data-dialog]");
      if (opener) {
        var dialog = document.getElementById(opener.getAttribute("data-dialog"));
        if (dialog && typeof dialog.showModal === "function") {
          evt.preventDefault();
          dialog.showModal();
        }
        return;
      }
      var closer = evt.target.closest("[data-close-dialog]");
      if (closer) {
        var parent = closer.closest("dialog");
        if (parent) { evt.preventDefault(); parent.close(); }
      }
      var account = document.querySelector("details.account[open]");
      if (account && !account.contains(evt.target)) account.removeAttribute("open");
    });

    document.addEventListener("change", function (evt) {
      var input = evt.target;
      if (!input.matches || !input.matches("[data-file-input]")) return;
      var drop = input.closest(".drop");
      var name = input.files && input.files[0] ? input.files[0].name : "";
      if (drop) drop.classList.toggle("has-file", !!name);
      var label = drop ? drop.querySelector("[data-file-name]") : null;
      if (label) label.textContent = name;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
