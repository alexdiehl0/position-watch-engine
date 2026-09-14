"""The daily run, end to end, as plain code with one Claude call.

    python -m position_watch daily [--pages-dir DIR] [--no-email]

Run by the GitHub Actions cron job (.github/workflows/daily.yml), which then
commits the workspace and pushes the locked dashboard. Steps:

  1. check secrets            6. write the report
  2. read yesterday's notes   7. build the dashboard
  3. read feedback (Gmail)    8. publish the locked copy (if --pages-dir)
  4. gather evidence          9. email the summary
  5. decide calls (Claude) and save them, the history, handoff, preferences

If a step fails, the run writes a short failure note (<date>-review-FAILED.md), emails
"AI STOCK PORTFOLIO REVIEW — <date> — FAILED" with the step and the (redacted) error,
and exits non-zero so the workflow shows it.
"""

import json
from datetime import datetime, timezone

from position_watch import handoff, llm, mail, people, reasoning, review, settings, suggestion_log
from position_watch.analysis import pnl
from position_watch.dashboard import publish, render
from position_watch.dashboard.view import load_inputs
from position_watch.documents import render as documents


class StepFailed(RuntimeError):
    def __init__(self, step, error):
        super().__init__(f"{step}: {error}")
        self.step, self.error = step, error


def _suggestions(day: str, calls: dict) -> dict:
    def table(entries):
        return {c["symbol"]: {"action": c["action"], "one_line": c["one_line"]} for c in entries}

    return {
        "date": day,
        "holdings": table(calls["holdings"]),
        "etfs": table(calls["etfs"]),
        "candidates": table(calls["candidates"]),
    }


def _handoff(day: str, calls: dict, feedback: list) -> dict:
    return {
        "date": day,
        "holdings_summary": {
            c["symbol"]: {"action": c["action"], "note": c["watch_note"]}
            for c in calls["holdings"] + calls["etfs"] + calls["candidates"]
        },
        "notes_for_tomorrow": calls["notes_for_tomorrow"],
        "feedback_incorporated": [f["message_id"] for f in calls["feedback_applied"]],
        "feedback_read": [m["message_id"] for m in feedback],
    }


def run(pages_dir=None, send_email=True, client=None, today=None, mode=None) -> dict:
    day = today or datetime.now(timezone.utc).date().isoformat()
    notes, current = [], {"step": "start"}

    def step(name):
        current["step"] = name
        print(f"· {name}", flush=True)

    try:
        step("check secrets")
        status = settings.secret_status()
        needed = (
            list(settings.REQUIRED_SECRETS)
            + ["ANTHROPIC_API_KEY"]
            + (["MAIL_USER", "MAIL_APP_PASSWORD"] if send_email else [])
        )
        missing = [n for n in needed if not status.get(n)]
        if missing:
            raise RuntimeError(f"missing secrets: {', '.join(missing)}")

        step("read yesterday's notes")
        yesterday = handoff.load_handoff()

        step("read feedback")
        try:
            feedback = mail.fetch_feedback() if send_email else []
        except Exception as exc:  # feedback is optional; the review still runs
            feedback = []
            notes.append(f"feedback not read today: {settings.redact(exc)}")

        step("gather evidence")
        results = review.run()
        review.save(results)

        step("decide calls")
        calls, usage = reasoning.decide(results, yesterday, feedback, day, client=client, mode=mode)
        llm.log_usage(day, {**usage, "task": "daily"})

        step("save calls")
        s = _suggestions(day, calls)
        settings.suggestions_path().write_text(json.dumps(s, indent=2) + "\n")
        suggestion_log.append_entries(day, s["holdings"], s["candidates"], s["etfs"])
        prefs_changed = reasoning.apply_preference_changes(calls, feedback)
        if feedback:
            mail.mark_used(feedback, day)
        handoff.save_handoff(_handoff(day, calls, feedback))

        step("write report")
        holdings, latest, _ = load_inputs()
        totals = pnl.compute(holdings, latest, latest.get("fx"))["totals"]
        summary_line = pnl.summary_line(totals)
        report_path = settings.reports_dir() / f"{day}-review.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            documents.report(
                date=day,
                review=results,
                calls=calls,
                summary_line=summary_line,
                run=results["run"],
                model=usage["model"],
            )  # fmt: skip
        )

        step("build dashboard")
        render.write_site()

        published = False
        if pages_dir:
            step("publish dashboard")
            try:
                publish.publish(pages_dir)
                published = True
            except publish.PasscodeMissing as exc:
                notes.append(str(exc))

        if send_email:
            step("send email")
            msg = documents.email(day, results, calls, totals, report_url=settings.report_url(day),
                                  dashboard_url=publish.email_link(), dashboard_published=published, notes=notes,
                                  names={r["symbol"]: r.get("name") for r in holdings})  # fmt: skip
            mail.send(people.recipients(), msg["subject"], msg["text"], html=msg["html"])

        return {"date": day, "model": usage["model"], "tokens": usage, "estimated_usd": usage["estimated_usd"],
                "report": str(report_path), "dashboard_published": published,
                "feedback_read": len(feedback), "preference_changes": prefs_changed, "notes": notes}  # fmt: skip

    except Exception as exc:
        error = settings.redact(f"{type(exc).__name__}: {exc}")
        _report_failure(day, current["step"], error, send_email)
        raise StepFailed(current["step"], error) from exc


def _report_failure(day: str, step: str, error: str, send_email: bool):
    text = (
        f"# Daily Portfolio Review — {day} — FAILED\n\n"
        f"The run stopped at **{step}**.\n\n```\n{error}\n```\n\n"
        "No calls were made today; yesterday's report and dashboard still stand.\n"
    )
    # Its own file, so a failed re-run never overwrites the day's real report.
    path = settings.reports_dir() / f"{day}-review-FAILED.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if send_email:
        try:
            mail.send(people.recipients(), documents.subject(day, "FAILED"),
                      f"Today's review stopped at: {step}\n\nError: {error}\n\n"
                      "Nothing was sent to the dashboard today. The workflow log has the details.")  # fmt: skip
        except Exception as mail_exc:  # the workflow still fails visibly
            print(settings.redact(f"could not send the FAILED email: {mail_exc}"))
