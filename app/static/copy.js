// The essay page's copy button. Nothing else on the site runs a script.
(function () {
  var b = document.querySelector(".copy-btn");
  if (!b) return;
  var box = document.getElementById(b.dataset.target);
  var label = b.textContent;
  function say(t) { b.textContent = t; setTimeout(function () { b.textContent = label; }, 2600); }
  function fallback() {
    box.parentNode.open = true; box.select();
    try { say(document.execCommand("copy") ? b.dataset.done : b.dataset.failed); }
    catch (e) { say(b.dataset.failed); }
  }
  b.hidden = false;
  b.addEventListener("click", function () {
    if (navigator.clipboard && window.isSecureContext)
      navigator.clipboard.writeText(box.value).then(function () { say(b.dataset.done); }, fallback);
    else fallback();
  });
})();
