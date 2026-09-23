"""Optional browser regression: run with Playwright and Chromium installed."""

import pytest

from potodo_docs_priority.html_report import (
    HtmlItem,
    HtmlMetrics,
    HtmlSection,
    _ranked_items,
    render_html,
)

playwright = pytest.importorskip("playwright.sync_api")


def test_weight_tuning_reorders_updates_hints_and_resets(tmp_path):
    items = [
        HtmlItem(
            "guide",
            "Guide",
            "https://example.test/guide",
            HtmlMetrics(
                priority=200 / 3,
                completion=50,
                original_visitors=0,
                document_score=(0,),
            ),
        ),
        HtmlItem(
            "bugs",
            "Bugs",
            "https://example.test/bugs",
            HtmlMetrics(
                priority=100 / 3 + 25,
                completion=0,
                original_visitors=100,
                document_score=(1,),
                core_boost=25,
            ),
        ),
    ]
    page_path = tmp_path / "index.html"
    page_path.write_text(
        render_html(
            HtmlSection("cpython", "CPython", _ranked_items(items, None)),
            "Priorities",
        ),
        encoding="utf-8",
    )
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(page_path.as_uri())
        rows = page.locator("#resources > li")

        def resources():
            return rows.evaluate_all("rows => rows.map(row => row.dataset.resource)")

        assert page.locator("#weights").is_visible()
        assert page.locator("input[data-metric]").count() == 3
        assert resources() == ["guide", "bugs"]
        original_scores = rows.locator(".priority").all_text_contents()
        page.get_by_role("button", name="Reset weights").click()
        assert resources() == ["guide", "bugs"]
        assert rows.locator(".priority").all_text_contents() == original_scores

        page.get_by_label("Completion", exact=True).fill("0")
        page.get_by_label("Navigation proximity", exact=True).fill("0")
        assert resources() == ["bugs", "guide"]
        assert rows.first.locator(".priority").inner_text() == "125.00"
        hint = rows.first.locator(".metric-hint")
        assert hint.get_attribute("title").startswith("Priority: 125.00;")
        assert hint.get_attribute("aria-label") == hint.get_attribute("title")
        page.get_by_label("Core resources boost (points)").fill("0")
        assert rows.first.locator(".priority").inner_text() == "100.00"

        page.get_by_label("Original visitors", exact=True).fill("0")
        assert resources() == ["bugs", "guide"]
        assert rows.locator(".priority").all_text_contents() == ["0.00", "0.00"]
        assert "All metric weights are zero" in page.get_by_role("status").inner_text()
        page.get_by_label("Completion", exact=True).fill("-1")
        assert "non-negative" in page.get_by_role("status").inner_text()
        assert rows.locator(".priority").all_text_contents() == ["0.00", "0.00"]
        page.get_by_label("Completion", exact=True).fill("")
        assert "non-negative" in page.get_by_role("status").inner_text()

        page.get_by_role("button", name="Reset weights").click()
        assert resources() == ["guide", "bugs"]
        assert rows.locator(".priority").all_text_contents() == original_scores
        assert not errors
        browser.close()


@pytest.mark.parametrize("count", [0, 15, 16, 20])
def test_resource_expansion_follows_tuned_ranking(tmp_path, count):
    items = [
        HtmlItem(
            f"page{index:02}",
            f"Page {index}",
            "https://example.test/",
            HtmlMetrics(
                priority=100 - index, completion=index * 4, document_score=(index,)
            ),
        )
        for index in range(count)
    ]
    page_path = tmp_path / "sphinx.html"
    page_path.write_text(
        render_html(
            HtmlSection("sphinx", "Sphinx", _ranked_items(items, None)),
            "Priorities",
        ),
        encoding="utf-8",
    )
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(page_path.as_uri())
        rows = page.locator("#resources > li")
        visible = page.locator("#resources > li:visible")
        toggle = page.locator("#show-more")
        assert rows.count() == count
        assert visible.count() == min(count, 15)
        if count <= 15:
            assert toggle.is_hidden()
        else:
            assert toggle.get_attribute("aria-expanded") == "false"
            # A previously hidden resource must enter the visible top 15.
            page.get_by_label("Navigation proximity", exact=True).fill("0")
            assert visible.count() == 15
            assert visible.first.get_attribute("data-resource") == f"page{count - 1:02}"
            toggle.click()
            assert visible.count() == count
            assert toggle.get_attribute("aria-expanded") == "true"
            assert toggle.inner_text() == "Show first 15"
            page.get_by_role("button", name="Reset weights").click()
            assert visible.count() == count
            assert visible.first.get_attribute("data-resource") == "page00"
            toggle.click()
            assert visible.count() == 15
            assert toggle.get_attribute("aria-expanded") == "false"
            assert toggle.inner_text() == f"Show all ({count})"
        assert not errors
        browser.close()
