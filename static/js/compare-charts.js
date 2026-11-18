// Comparison charts (Chart.js, vendored). Small multiples: one horizontal bar chart per
// nutrient, each with its own scale (never one shared axis for kJ, kcal and grams).
// Colours follow the product's column (fixed categorical order), never its rank.
// Every bar carries its value as a text label; the comparison table holds all values.

const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]; // validated: CVD ΔE ≥ 9.2, normal ≥ 27.6
const INK = { primary: "#14211d", secondary: "#4f625c", muted: "#7b8a85", grid: "#e1e7e5" };
const FONT = 'Inter, "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

// Icons come from the same local Lucide sprite as the rest of the page.
const SPRITE = (document.querySelector("svg.icon use")?.getAttribute("href") || "").split("#")[0];
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `${SPRITE}#${name}`);
  svg.append(use);
  return svg;
}

function short(name, max = 22) {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

// Value label at the end of each bar (text ink, not the series colour).
const valueLabels = {
  id: "valueLabels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    const meta = chart.getDatasetMeta(0);
    const display = chart.data.datasets[0].display;
    ctx.save();
    ctx.font = `600 12px ${FONT}`;
    ctx.fillStyle = INK.primary;
    ctx.textBaseline = "middle";
    meta.data.forEach((bar, i) => {
      const text = display[i] || "—";
      const value = chart.data.datasets[0].data[i];
      const x = value == null ? chart.scales.x.left : bar.x;
      ctx.fillStyle = value == null ? INK.muted : INK.primary;
      ctx.fillText(text, x + 6, bar.y);
    });
    ctx.restore();
  },
};

function renderPanel(container, panel, group) {
  const card = el("figure", "chart-panel");
  const caption = el("figcaption", "chart-title");
  const name = el("span", "chart-name");
  name.append(icon(panel.icon), el("span", "", panel.label));
  caption.append(name, el("span", "chart-unit", panel.unit));
  const wrap = el("div", "chart-canvas");
  wrap.style.height = `${group.products.length * 30 + 26}px`;
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute(
    "aria-label",
    `${panel.label}: ` + group.products.map((p, i) => `${p} ${panel.display[i] || "not available"}`).join(", ")
  );
  wrap.append(canvas);
  const foot = el("p", "chart-diff");
  if (panel.difference) {
    foot.append(el("span", "chart-diff-label", "Difference"), el("span", "chart-diff-value", panel.difference));
  } else {
    foot.append(el("span", "chart-diff-label", "Same value"));
  }
  card.append(caption, wrap, foot);
  container.append(card);

  const max = Math.max(...panel.values.filter((v) => v != null), 0);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // eslint-disable-next-line no-new
  new window.Chart(canvas, {
    type: "bar",
    data: {
      labels: group.products.map((p) => short(p, 14)),
      datasets: [
        {
          data: panel.values,
          display: panel.display,
          backgroundColor: group.slots.map((s) => SERIES[s % SERIES.length]),
          borderRadius: { topRight: 4, bottomRight: 4 }, // rounded data end, square at baseline
          borderSkipped: "start",
          barThickness: 14,
          minBarLength: 2,
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      animation: reduceMotion ? false : { duration: 350 },
      layout: { padding: { right: 72 } }, // room for the value label
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#14211d",
          titleFont: { family: FONT, weight: "700" },
          bodyFont: { family: FONT },
          padding: 10,
          displayColors: true,
          callbacks: {
            title: (items) => group.products[items[0].dataIndex],
            label: (item) => ` ${panel.label}: ${panel.display[item.dataIndex] || "not available"}`,
          },
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          suggestedMax: max * 1.05 || 1,
          grid: { color: INK.grid, drawTicks: false },
          border: { display: false },
          ticks: { display: false },
        },
        y: {
          grid: { display: false },
          border: { color: "#c3cdc9" },
          ticks: { color: INK.secondary, font: { family: FONT, size: 12 }, padding: 6, crossAlign: "far" },
        },
      },
    },
    plugins: [valueLabels],
  });
}

function render() {
  const data = JSON.parse(document.getElementById("compare-charts-data").textContent);
  const root = document.querySelector("[data-compare-charts]");
  if (!root || !window.Chart) return;
  root.replaceChildren();
  data.forEach((group) => {
    const section = el("section", "chart-group");
    section.append(el("h3", "chart-group-title", group.title));
    const legend = el("ul", "chart-legend");
    group.products.forEach((name, i) => {
      const li = el("li");
      const swatch = el("span", "swatch");
      swatch.dataset.slot = String(group.slots[i] % SERIES.length);
      li.append(swatch, el("span", "", name));
      legend.append(li);
    });
    const grid = el("div", "chart-grid");
    section.append(legend, grid);
    root.append(section);
    group.panels.forEach((panel) => renderPanel(grid, panel, group));
  });
}

// chart.umd.min.js is a deferred classic script: it has run before module scripts execute.
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", render);
else render();
