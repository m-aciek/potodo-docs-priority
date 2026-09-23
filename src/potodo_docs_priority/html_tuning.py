"""Self-contained browser controls for the generated priority reports."""

TUNING_SCRIPT = """
<script>
(() => {
  const form = document.querySelector('#weights');
  if (!form) return;
  const tableBody = document.querySelector('#resources');
  const status = document.querySelector('#tuning-status');
  const controls = [...form.querySelectorAll('input[data-metric]')];
  const boost = form.querySelector('#core-boost');
  const inputs = boost ? [...controls, boost] : controls;
  const storageKey = `potodo-docs-priority:weights:${form.dataset.project}`;
  const inputKey = input => input.dataset.metric ?? 'core_boost';
  const toggle = document.querySelector('#show-more');
  const visibleLimit = 15;
  let expanded = false;
  const rows = [...tableBody.children].map((element, initialIndex) => ({
    element,
    initialIndex,
    resource: element.dataset.resource,
    ranks: JSON.parse(element.dataset.ranks),
    core: element.dataset.core === 'true',
    priority: Number(element.dataset.priority),
    initialPriority: Number(element.dataset.priority),
    score: element.querySelector('.priority'),
  }));
  function updateVisibility() {
    rows.forEach((row, index) => {
      row.element.hidden = !expanded && index >= visibleLimit;
    });
    toggle.hidden = rows.length <= visibleLimit;
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.textContent = expanded ? `Show first ${visibleLimit}` :
      `Show all (${rows.length})`;
  }
  toggle.addEventListener('click', () => {
    expanded = !expanded;
    updateVisibility();
  });
  function update() {
    if (inputs.some(input => !input.validity.valid ||
        input.value === '' || !Number.isFinite(input.valueAsNumber))) {
      status.textContent = 'Enter a non-negative number for every weight.';
      return;
    }
    const weights = Object.fromEntries(controls.map(input =>
      [input.dataset.metric, input.valueAsNumber]));
    const defaults = inputs.every(input => input.valueAsNumber === Number(input.defaultValue));
    // Scale first to keep even very large finite weights numerically stable.
    const scale = Math.max(1, ...Object.values(weights));
    for (const key of Object.keys(weights)) weights[key] /= scale;
    for (const row of rows) {
      let total = 0;
      let weighted = 0;
      for (const [metric, rank] of Object.entries(row.ranks)) {
        const weight = weights[metric] ?? 0;
        total += weight;
        weighted += rank * weight;
      }
      // Restore Python's exact scores at defaults, including its sum rounding.
      row.priority = defaults ? row.initialPriority :
        (total ? 100 * weighted / total : 0) +
        (row.core && boost ? boost.valueAsNumber : 0);
      const score = row.priority.toFixed(2);
      row.element.dataset.priority = String(row.priority);
      row.score.textContent = score;
    }
    rows.sort((a, b) => defaults ? a.initialIndex - b.initialIndex :
      b.priority - a.priority ||
      (a.resource < b.resource ? -1 : a.resource > b.resource ? 1 : 0));
    const fragment = document.createDocumentFragment();
    for (const row of rows) fragment.append(row.element);
    tableBody.append(fragment);
    updateVisibility();
    status.textContent = `${rows.length} unfinished resources. ` +
      (Object.values(weights).some(weight => weight > 0)
        ? 'Ranking updated.' : 'All metric weights are zero; only the core boost applies.');
    try {
      localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(
        inputs.map(input => [inputKey(input), input.valueAsNumber]))));
    } catch {
      // Tuning remains available when browser storage is disabled or full.
    }
  }
  form.hidden = false;
  updateVisibility();
  let restored = false;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    for (const input of inputs) {
      const value = saved?.[inputKey(input)];
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
        input.value = String(value);
        restored = true;
      }
    }
  } catch {
    // Ignore unreadable storage and keep the project's defaults.
  }
  if (restored) update();
  form.addEventListener('input', update);
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('reset', event => {
    event.preventDefault();
    for (const input of form.querySelectorAll('input')) input.value = input.defaultValue;
    update();
  });
})();
</script>
"""
