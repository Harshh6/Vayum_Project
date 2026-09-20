(function () {
  if (typeof Chart === "undefined" || !window.VAYUM_HISTORY) return;

  var history = window.VAYUM_HISTORY;
  var chart = document.getElementById("historyDetailChart");
  if (!chart) return;

  new Chart(chart, {
    type: "line",
    data: {
      labels: history.labels,
      datasets: [{
        label: "Overall Air Quality",
        data: history.values,
        borderColor: "#19715A",
        backgroundColor: "#19715A22",
        borderWidth: 2,
        pointRadius: 4,
        pointBackgroundColor: "#19715A",
        tension: 0.3,
        fill: true
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, title: { display: true, text: "Air Quality" } } }
    }
  });
})();