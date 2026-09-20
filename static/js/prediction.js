(function () {
  if (typeof Chart === "undefined" || !window.VAYUM_PREDICTION) return;

  var forecast = window.VAYUM_PREDICTION;
  var colors = ["#19715A", "#2A7FC9", "#D99A10", "#E0742A", "#9B3A6A", "#3FA34D"];
  var options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: true, position: "bottom" } },
    scales: { x: { ticks: { maxTicksLimit: 12 } }, y: { beginAtZero: true } }
  };

  var overall = document.getElementById("overallPredictionChart");
  if (overall) {
    new Chart(overall, {
      type: "line",
      data: {
        labels: forecast.labels,
        datasets: [{ label: "Overall Air Quality", data: forecast.values, borderColor: "#19715A", backgroundColor: "#19715A22", fill: true, tension: 0.3 }]
      },
      options: options
    });
  }

  var pollutants = document.getElementById("pollutantPredictionChart");
  if (pollutants) {
    new Chart(pollutants, {
      type: "line",
      data: {
        labels: forecast.labels,
        datasets: forecast.pollutants.map(function (pollutant, index) {
          return { label: pollutant.name, data: pollutant.values, borderColor: colors[index % colors.length], borderWidth: 2, pointRadius: 1, tension: 0.25 };
        })
      },
      options: options
    });
  }
})();