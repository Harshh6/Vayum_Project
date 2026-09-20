/* Vayum — dashboard charts (Chart.js).
   Data arrives from Flask through window.VAYUM in dashboard.html. */

(function () {
  if (typeof Chart === "undefined" || !window.VAYUM) return;

  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.color = "#5E6B67";
  Chart.defaults.font.size = 12;

  var grid = { color: "#E3E7E2", drawTicks: false };
  var baseOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false } },
      y: { beginAtZero: true, grid: grid, title: { display: true, text: "AQI" } }
    }
  };

  function fill(ctx, color) {
    var g = ctx.createLinearGradient(0, 0, 0, 260);
    g.addColorStop(0, color + "33");
    g.addColorStop(1, color + "00");
    return g;
  }

  /* Last 7 days */
  var h = document.getElementById("historyChart");
  if (h) {
    new Chart(h, {
      type: "line",
      data: {
        labels: window.VAYUM.history.labels,
        datasets: [{
          data: window.VAYUM.history.values,
          borderColor: "#19715A",
          backgroundColor: fill(h.getContext("2d"), "#19715A"),
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: "#19715A",
          tension: 0.3,
          fill: true
        }]
      },
      options: baseOptions
    });
  }

  /* Next 24 hours — prediction, drawn dashed so it reads as a forecast */
  var p = document.getElementById("predictionChart");
  if (p) {
    new Chart(p, {
      type: "line",
      data: {
        labels: window.VAYUM.prediction.labels,
        datasets: [{
          data: window.VAYUM.prediction.values,
          borderColor: "#2A7FC9",
          backgroundColor: fill(p.getContext("2d"), "#2A7FC9"),
          borderWidth: 2,
          borderDash: [5, 4],
          pointRadius: 0,
          tension: 0.35,
          fill: true
        }]
      },
      options: Object.assign({}, baseOptions, {
        scales: Object.assign({}, baseOptions.scales, {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }
        })
      })
    });
  }
})();
