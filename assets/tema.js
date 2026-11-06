/* Comportamento comum: tema claro/escuro, revelar ao rolar e registro de erros.
   Carregue no <head> com defer. Expõe window.Tema. */
(function () {
  var raiz = document.documentElement;
  // erros de script ficam marcados no <html>: o teste de fumaça das páginas lê isso
  window.addEventListener("error", function (e) {
    raiz.dataset.erro = (raiz.dataset.erro ? raiz.dataset.erro + " | " : "") + (e.message || "erro");
  });
  try { var t = localStorage.getItem("pdc-tema"); if (t) raiz.dataset.theme = t; } catch (e) {}

  function escuro() {
    return raiz.dataset.theme ? raiz.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function alternar() {
    raiz.dataset.theme = escuro() ? "light" : "dark";
    try { localStorage.setItem("pdc-tema", raiz.dataset.theme); } catch (e) {}
    document.dispatchEvent(new CustomEvent("tema"));
  }
  var reduzir = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function revelar() {
    var els = document.querySelectorAll("[data-revelar]:not(.visivel)");
    if (!("IntersectionObserver" in window) || reduzir) { els.forEach(function (el) { el.classList.add("visivel"); }); return; }
    var io = new IntersectionObserver(function (entradas) {
      entradas.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("visivel"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (el) { io.observe(el); });
  }

  // anima um número de a até b (ease-out) chamando fn(valor) a cada quadro
  function animarNumero(a, b, ms, fn) {
    if (reduzir || ms <= 0) { fn(b); return; }
    var t0 = performance.now();
    function passo(t) {
      var k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      fn(a + (b - a) * e);
      if (k < 1) requestAnimationFrame(passo);
    }
    requestAnimationFrame(passo);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-tema-alternar]").forEach(function (b) { b.addEventListener("click", alternar); });
    revelar();
  });

  window.Tema = { escuro: escuro, alternar: alternar, revelar: revelar, animarNumero: animarNumero, reduzir: reduzir };
})();
