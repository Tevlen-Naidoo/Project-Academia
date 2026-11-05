// Filters the archive by topic, kind and title words. The page works without it.
(function () {
  "use strict";
  var form = document.querySelector("form.filters");
  if (!form) return;
  form.hidden = false;
  var days = Array.prototype.slice.call(document.querySelectorAll("ol.days > li"));
  var empty = document.querySelector("p.empty");

  function apply() {
    var topic = form.topic.value;
    var funnel = form.funnel.value;
    var words = form.q.value.toLowerCase().split(/\s+/).filter(Boolean);
    var shown = 0;
    days.forEach(function (day) {
      var any = false;
      day.querySelectorAll("li[data-topic]").forEach(function (item) {
        var title = item.textContent.toLowerCase();
        var ok =
          (!topic || item.dataset.topic === topic) &&
          (!funnel || item.dataset.funnel === funnel) &&
          words.every(function (w) { return title.indexOf(w) !== -1; });
        item.hidden = !ok;
        any = any || ok;
      });
      day.hidden = !any;
      if (any) shown += 1;
    });
    if (empty) empty.hidden = shown > 0;
  }

  form.addEventListener("input", apply);
  form.addEventListener("submit", function (e) { e.preventDefault(); });
})();
