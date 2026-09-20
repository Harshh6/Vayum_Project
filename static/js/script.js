/* Vayum — shared UI behaviour: dependent dropdowns, navbar, gauge needle. */

document.addEventListener("DOMContentLoaded", function () {
  setupGateForm();
  setupNavLocation();
  setupNavToggle();
  setupGauge();
  setupWeatherCards();
  setupPollutantCards();
});

/* Fetch cities of a state from the Flask API. */
function fetchCities(state) {
  return fetch("/api/cities/" + encodeURIComponent(state))
    .then(function (r) { return r.ok ? r.json() : []; })
    .catch(function () { return []; });
}

function fillSelect(select, cities, selected) {
  select.innerHTML = "";
  cities.forEach(function (city) {
    var opt = document.createElement("option");
    opt.value = city;
    opt.textContent = city;
    if (city === selected) opt.selected = true;
    select.appendChild(opt);
  });
  select.disabled = cities.length === 0;
}

/* --- Page 1: state -> city -> Continue -------------------------------- */
function setupGateForm() {
  var stateSel = document.getElementById("gateState");
  var citySel = document.getElementById("gateCity");
  var go = document.getElementById("gateGo");
  if (!stateSel || !citySel) return;

  stateSel.addEventListener("change", function () {
    citySel.innerHTML = '<option value="" disabled selected>Loading cities…</option>';
    citySel.disabled = true;
    go.disabled = true;
    fetchCities(stateSel.value).then(function (cities) {
      citySel.innerHTML = '<option value="" disabled selected>Choose a city</option>';
      cities.forEach(function (city) {
        var opt = document.createElement("option");
        opt.value = city;
        opt.textContent = city;
        citySel.appendChild(opt);
      });
      citySel.disabled = false;
    });
  });

  citySel.addEventListener("change", function () {
    go.disabled = !citySel.value;
  });
}

/* --- Navbar location picker ------------------------------------------ */
function setupNavLocation() {
  var stateSel = document.getElementById("navState");
  var citySel = document.getElementById("navCity");
  if (!stateSel || !citySel) return;

  var current = citySel.dataset.selected || "";
  fetchCities(stateSel.value).then(function (cities) {
    fillSelect(citySel, cities, current);
  });

  stateSel.addEventListener("change", function () {
    fetchCities(stateSel.value).then(function (cities) {
      fillSelect(citySel, cities, "");
    });
  });
}

function setupNavToggle() {
  var btn = document.getElementById("navToggle");
  var links = document.getElementById("navLinks");
  if (!btn || !links) return;
  btn.addEventListener("click", function () {
    links.classList.toggle("is-open");
  });
}

/* --- Gauge needle ------------------------------------------------------ */
function setupGauge() {
  var gauge = document.querySelector(".gauge");
  var needle = document.getElementById("needle");
  if (!gauge || !needle) return;

  var aqi = Math.max(0, Math.min(500, parseInt(gauge.dataset.aqi, 10) || 0));
  var angle = (aqi / 500) * 180 - 90;   // -90deg at 0 AQI, +90deg at 500

  // start flat on the left, then swing to the reading
  window.requestAnimationFrame(function () {
    needle.setAttribute("transform", "rotate(" + angle + " 160 170)");
  });
}

function setupWeatherCards() {
  document.querySelectorAll(".wx").forEach(function (card) {
    function toggleCard() {
      var expanded = card.classList.toggle("is-expanded");
      card.setAttribute("aria-expanded", expanded ? "true" : "false");
    }

    card.addEventListener("click", toggleCard);
    card.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleCard();
      }
    });
  });
}

function setupPollutantCards() {
  document.querySelectorAll(".pollutant").forEach(function (card) {
    function toggleCard() {
      var expanded = card.classList.toggle("is-expanded");
      card.setAttribute("aria-expanded", expanded ? "true" : "false");
    }

    card.addEventListener("click", toggleCard);
    card.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleCard();
      }
    });
  });
}
