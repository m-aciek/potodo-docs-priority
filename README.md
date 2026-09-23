# potodo-docs-priority

Prioritize translation resources for CPython, Sphinx, and the Python Packaging
User Guide using Potodo's source-word-weighted completion metric.

```sh
pip install potodo-docs-priority
```

The command has a preset for each supported project's source, translation,
gettext, URL, and analytics conventions:

```sh
# CPython: pass the repository root and the PO repository.
potodo-docs-priority ../python-docs-pl \
  --project cpython \
  --source ../cpython \
  --language pl \
  --plausible-stats ../plausible-stats

# Sphinx: pass doc/ and the locales root. No analytics are required.
potodo-docs-priority ../sphinx-doc-translations/locales \
  --project sphinx \
  --source ../sphinx/doc \
  --language pl_PL

# Packaging: run from a checkout containing translation/source's locales/.
potodo-docs-priority ../packaging.python.org/locales \
  --project packaging \
  --source ../packaging.python.org/source \
  --language ja \
  --plausible-stats ../plausible-stats
```

For locale repositories, the command automatically selects
`<language>/LC_MESSAGES`. Packaging's `gettext_compact = "messages"` layout is
represented by its single `messages.po`; traffic from all source documents is
summed for that catalog. Locale names are normalized for public URLs, so for
example `pt_BR` uses the `/pt-br/latest/` analytics prefix.

## Priority model

Every available metric receives equal weight after percentile ranking:

- visitors to the original article;
- visitors to the translated article;
- navigation distance from the documentation index;
- source-word-weighted completion, with resources nearer 100% ranked higher.

CPython's core resources (`bugs`, `tutorial/*`, and `builtins/functions`)
receive an additional 25 priority points after the weighted average. The
result can exceed 100; priority is a score, not a percentage.

CPython and Packaging popularity is summed over the latest 30 Plausible
snapshots by default. Sphinx has no popularity dataset, so its priority uses
navigation distance and completion, weighted 100:1 to keep its documentation
homepage and other primary navigation resources ahead of more complete but
less central pages. If a Packaging translation has no localized Plausible
prefix, translated popularity is omitted while original popularity remains in
use.

The navigation scorer is internal. CPython retains its custom
`indexcontent.html` big-link ordering, and its shared `sphinx.po` template
catalog is scored at the landing-page root and receives the main-page traffic;
conventional projects start with `index.rst` and follow `toctree` and `include`
directives. Use
`--stats-snapshots N`, `--docs-version VERSION` (CPython), or `--json` to adjust
the report. Compatible precomputed navigation JSON can be supplied with
`--document-scores` instead of `--source`.

Pass a current gettext catalog directory with `--pot` to calculate completion
after merging translations with source templates. For example, Sphinx's
translation repository uses `--pot ../sphinx-doc-translations/locales/pot`.

## Linked HTML report

Install the optional checksum dependency and build pages containing all
unfinished resources for each project in priority order:

```sh
pip install 'potodo-docs-priority[html]'

potodo-docs-priority-html \
  --language pl \
  --sphinx-language pl_PL \
  --output priorities
```

The default paths match sibling checkouts under `~/projects`; every source,
translation, and stats path also has a corresponding command-line option.
The page initially shows the top 15 resources. **Show all** reveals the rest;
**Show first 15** collapses the list again. Weight changes rank all included
resources, so hidden resources can move into the visible top 15.
Use `--limit N` to restrict each project to its N highest-priority resources.
Packaging requires `../packaging.python.org/locales` from its
`translation/source` branch.

CPython uses exact Transifex resource names from `.tx/config`, Sphinx derives
its Transifex names from catalog paths, and Packaging's compact catalog is
split by source-document occurrences. Packaging links use Weblate's unit
checksums and point at the document heading when available. Document headings
are translated when the PO contains a translation. Weblate's unfinished-state
filter is omitted when the linked heading is already translated.

The output directory uses the CPython report as `index.html` and creates
`packaging.html` and `sphinx.html` for the other selected projects. If CPython
is excluded with `--projects`, `index.html` instead links the selected project
pages.

Resources appear in a table with separate columns for priority, completion,
navigation distance, and available original/translated traffic. Completion is
shown as a percentage alongside the other metrics. On narrow screens, the table
can be scrolled horizontally.

The HTML weight controls recalculate scores and reorder resources immediately.
Each available metric has a non-negative weight; zero disables that metric.
CPython also has an adjustable core-resource boost. With every metric disabled,
only the boost contributes, and ties are ordered by resource name. Reset restores
the project's defaults. Tuning applies to all included resources; with `--limit`,
their percentile ranks still come from the full set of unfinished resources.
Weights and the core-resource boost are saved in the browser's local storage,
separately for each project, and restored on reload. Reset also saves the defaults.

The included GitHub Pages workflow rebuilds this Polish report every day at
03:17 UTC, on pushes to `main`, and on manual runs. After pushing the project
to GitHub, select **GitHub Actions** as the Pages build and deployment source
in the repository settings.

## Development

```sh
python -m pytest
```

The optional browser regression test also needs `playwright` and its Chromium
browser (`playwright install chromium`).
