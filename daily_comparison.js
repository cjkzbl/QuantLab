(() => {
  const root = document.querySelector("[data-daily-comparison]");
  if (!root) return;

  const chart = root.querySelector("[data-chart]");
  const status = root.querySelector("[data-chart-status]");
  const dayInput = root.querySelector("[data-day]");
  const readout = root.querySelector("[data-readout]");
  const presets = [...root.querySelectorAll("[data-months]")];
  const steps = [...root.querySelectorAll("[data-step]")];
  const series = [
    { name: "现行策略", color: "#62d9c2", dash: "solid" },
    { name: "防守清零", color: "#edb580", dash: "dash" },
    { name: "定投 QQQ", color: "#73aafa", dash: "dot" },
  ];
  const number = new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 4, maximumFractionDigits: 4, useGrouping: false,
  });
  const percent = new Intl.NumberFormat("zh-CN", {
    style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  const signed = new Intl.NumberFormat("zh-CN", {
    style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2,
    signDisplay: "exceptZero",
  });
  const NAV = 1;
  const DRAWDOWN = 4;
  const DAILY_RETURN = 7;

  let rows;
  try {
    rows = JSON.parse(root.querySelector("[data-observations]").textContent);
    if (!Array.isArray(rows) || rows.length === 0) throw new Error("No comparison rows");
  } catch {
    status.textContent = "暂时无法读取逐日对比数据。";
    return;
  }

  const dates = rows.map(row => row[0]);
  const first = dates[0];
  const last = dates.at(-1);
  const lastIndex = dates.length - 1;
  let selected = lastIndex;
  let activePreset = "3";
  let rendered = false;
  const Plotly = window.Plotly;

  function earlierDate(months) {
    const [year, month, day] = last.split("-").map(Number);
    const end = new Date(Date.UTC(year, month - 1 - months + 1, 0));
    end.setUTCDate(Math.min(day, end.getUTCDate()));
    return end.toISOString().slice(0, 10);
  }

  function presetRange(months) {
    const earlier = months === "all" ? first : earlierDate(Number(months));
    return [earlier < first ? first : earlier, last];
  }

  let visibleRange = presetRange(activePreset);

  function tradingDayAtOrBefore(date) {
    let low = 0;
    let high = lastIndex;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (dates[middle] <= date) low = middle;
      else high = middle - 1;
    }
    return low;
  }

  function plotRange([start, end]) {
    if (start !== end) return [start, end];
    // Center the one point when there are no other trading dates.
    const day = Date.parse(start + "T00:00:00Z");
    return [day - 43200000, day + 43200000];
  }

  function valueRanges([start, end]) {
    let low = Infinity;
    let high = -Infinity;
    let deepest = 0;
    const left = tradingDayAtOrBefore(start);
    const right = tradingDayAtOrBefore(end);
    for (let day = left; day <= right; day++) {
      const row = rows[day];
      for (let strategy = 0; strategy < series.length; strategy++) {
        low = Math.min(low, row[NAV + strategy]);
        high = Math.max(high, row[NAV + strategy]);
        deepest = Math.min(deepest, row[DRAWDOWN + strategy]);
      }
    }
    const pad = Math.max(high * .015, (high - low) * .075);
    return {
      "yaxis.range": [Math.max(0, low - pad), high + pad],
      "yaxis2.range": [Math.max(-1, deepest - .025), .01],
    };
  }

  function updatePresets() {
    for (const button of presets) {
      button.setAttribute("aria-pressed", String(button.dataset.months === activePreset));
      button.disabled = !rendered;
    }
  }

  function dateMarkers() {
    const date = dates[selected];
    if (date < visibleRange[0] || date > visibleRange[1]) return [];
    return [
      { xref: "x", y0: .51, y1: 1 },
      { xref: "x2", y0: 0, y1: .37 },
    ].map(panel => ({
      ...panel, type: "line", x0: date, x1: date, yref: "paper", layer: "above",
      line: { color: "#8297ad", width: 1, dash: "dot" },
    }));
  }

  function updateRange(range, preset = null) {
    visibleRange = range;
    activePreset = preset;
    updatePresets();
    if (!rendered) return;
    const plotted = plotRange(range);
    Plotly.relayout(chart, {
      "xaxis.range": plotted,
      "xaxis2.range": plotted,
      ...valueRanges(range),
      shapes: dateMarkers(),
    });
  }

  function showDay(index, scrollIntoRange) {
    selected = Math.min(lastIndex, Math.max(0, index));
    const row = rows[selected];
    const date = row[0];
    dayInput.value = date;
    readout.replaceChildren(...series.map((strategy, i) => {
      const result = document.createElement("div");
      const label = document.createElement("strong");
      label.textContent = strategy.name;
      label.style.color = strategy.color;
      const value = document.createElement("span");
      value.textContent = "净值 " + number.format(row[NAV + i]) +
        " · 日收益 " + signed.format(row[DAILY_RETURN + i]) +
        " · 回撤 " + percent.format(row[DRAWDOWN + i]);
      result.append(label, value);
      return result;
    }));
    for (const button of steps) {
      const next = selected + Number(button.dataset.step);
      button.disabled = next < 0 || next > lastIndex;
    }
    if (scrollIntoRange && (date < visibleRange[0] || date > visibleRange[1])) {
      const start = Math.max(0, Math.min(selected - 10, lastIndex - 20));
      updateRange([dates[start], dates[Math.min(start + 20, lastIndex)]]);
    } else if (rendered) {
      Plotly.relayout(chart, { shapes: dateMarkers() });
    }
  }

  function attachControls() {
    for (const preset of presets) {
      preset.addEventListener("click", () => {
        const months = preset.dataset.months;
        updateRange(presetRange(months), months);
      });
    }
    dayInput.addEventListener("change", () => {
      showDay(dayInput.value ? tradingDayAtOrBefore(dayInput.value) : selected, true);
    });
    for (const step of steps) {
      step.addEventListener("click", () => showDay(selected + Number(step.dataset.step), true));
    }
  }

  function panelTitle(text, y) {
    return {
      text, xref: "paper", x: 0, xanchor: "left", yref: "paper", y, yanchor: "bottom",
      showarrow: false, font: { size: 12, color: "#d9e5ef" },
    };
  }

  function tracesFor(drawdown) {
    return series.map((strategy, i) => ({
      type: "scatter", mode: dates.length === 1 ? "markers" : "lines",
      name: strategy.name, legendgroup: strategy.name, showlegend: !drawdown,
      xaxis: drawdown ? "x2" : "x", yaxis: drawdown ? "y2" : "y",
      x: dates, y: rows.map(row => row[(drawdown ? DRAWDOWN : NAV) + i]),
      customdata: rows.map(row => [row[NAV + i], row[DAILY_RETURN + i], row[DRAWDOWN + i]]),
      line: { color: strategy.color, width: i === 0 ? 2.15 : 1.7, dash: strategy.dash },
      marker: { color: strategy.color, size: 5 },
      hovertemplate: "%{customdata[0]:.4f}× · 日 %{customdata[1]:+.2%} · 回撤 %{customdata[2]:.2%}",
    }));
  }

  function layout() {
    const x = {
      type: "date", range: plotRange(visibleRange), hoverformat: "%Y-%m-%d",
      gridcolor: "#26364b", showline: true, linecolor: "#344761",
      tickfont: { size: 11 }, ticklabeloverflow: "hide past domain",
    };
    const y = {
      fixedrange: true, gridcolor: "#26364b", zerolinecolor: "#344761",
      tickfont: { size: 11 }, nticks: 5,
    };
    const ranges = valueRanges(visibleRange);
    return {
      autosize: true,
      margin: { l: 46, r: 12, t: 65, b: 54 },
      paper_bgcolor: "#101b2a", plot_bgcolor: "#101b2a",
      font: { family: '"Segoe UI", "Microsoft YaHei", system-ui, sans-serif', color: "#bccada", size: 11 },
      legend: { orientation: "h", x: 0, y: 1.20, font: { size: 11 } },
      dragmode: "zoom", hovermode: "x unified",
      hoverlabel: { bgcolor: "#142338", bordercolor: "#344761", font: { color: "#edf4f9", size: 11 }, namelength: -1 },
      xaxis: { ...x, anchor: "y", matches: "x2", showticklabels: false },
      xaxis2: {
        ...x, anchor: "y2",
        rangeslider: { bgcolor: "#142338", borderwidth: 1, bordercolor: "#344761", thickness: .11, visible: dates.length > 1 },
      },
      yaxis: { ...y, anchor: "x", domain: [.51, 1], range: ranges["yaxis.range"], ticksuffix: "×" },
      yaxis2: { ...y, anchor: "x2", domain: [0, .37], range: ranges["yaxis2.range"], tickformat: ".0%" },
      annotations: [panelTitle("累计净值", 1.035), panelTitle("逐日回撤", .40)],
      shapes: dateMarkers(),
    };
  }

  function trackManualRange(change) {
    for (const axis of ["xaxis", "xaxis2"]) {
      const range = change[axis + ".range"] ||
        [change[axis + ".range[0]"], change[axis + ".range[1]"]];
      const reset = change[axis + ".autorange"] === true;
      if (!reset && !range.every(value => typeof value === "string")) continue;
      const next = reset ? [first, last] : range.map(value => value.slice(0, 10));
      if (next[0] === visibleRange[0] && next[1] === visibleRange[1]) continue;
      visibleRange = next;
      activePreset = null;
      updatePresets();
      Plotly.relayout(chart, { ...valueRanges(visibleRange), shapes: dateMarkers() });
      return;
    }
  }

  function unavailable() {
    chart.hidden = true;
    status.textContent = "暂时无法加载曲线，可通过日期查看每日数值。";
  }

  updatePresets();
  attachControls();
  showDay(selected, false);
  if (!Plotly) {
    unavailable();
    return;
  }
  Plotly.newPlot(chart, [...tracesFor(false), ...tracesFor(true)], layout(), {
    responsive: true, displayModeBar: false, displaylogo: false, scrollZoom: false,
  }).then(() => {
    rendered = true;
    chart.on("plotly_relayout", trackManualRange);
    chart.on("plotly_click", event => {
      const point = event.points[0];
      if (point && typeof point.x === "string") showDay(tradingDayAtOrBefore(point.x.slice(0, 10)), false);
    });
    updateRange(visibleRange, activePreset);
    status.hidden = true;
  }).catch(unavailable);
})();
