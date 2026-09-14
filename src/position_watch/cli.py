"""Command line for everything the daily and monthly routines do.

    python -m position_watch <command>      (or `position-watch <command>` once installed)

Commands print JSON or short text and exit non-zero on failure, so a routine
can check each step. None of them ever prints a secret.
"""

import argparse
import json
import sys

from position_watch import compliance, handoff, people, review, settings, suggestion_log


def _print(data):
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _stdin_json():
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.exit(f"expected JSON on stdin: {exc}")


def cmd_check_env(args):
    status = settings.secret_status()
    for name, present in status.items():
        print(f"{name}: {'set' if present else 'MISSING'}")
    missing = [n for n in settings.REQUIRED_SECRETS if not status[n]]
    if missing:
        sys.exit(f"required secrets missing: {', '.join(missing)}")


def cmd_review(args):
    results = review.run(include_candidates=not args.no_watchlist)
    review.save(results)
    _print(review.summarize(results))


def cmd_pnl(args):
    from position_watch.analysis import pnl
    from position_watch.dashboard.view import load_inputs

    holdings, latest, _ = load_inputs()
    result = pnl.compute(holdings, latest, (latest or {}).get("fx"))
    _print(
        {"summary_line": pnl.summary_line(result["totals"]), "totals": result["totals"], "fx_used": result["fx_used"]}
    )


def cmd_compliance(args):
    _print({"note": compliance.NOTE, "action_labels": compliance.ACTION_LABELS})


def cmd_people(args):
    _print(people.load())


def cmd_recipients(args):
    _print(people.recipients())


def cmd_who_sent(args):
    _print(people.who_sent(args.email))


def cmd_prefs_update(args):
    """Changes as JSON on stdin, e.g. {"avoid": {"add": ["China exposure"]}}."""
    _print({"recorded": people.update_preferences(_stdin_json(), source=args.source)})


def cmd_handoff(args):
    if args.action == "save":
        handoff.save_handoff(_stdin_json())
        print(f"saved {handoff.handoff_path()}")
    else:
        _print(handoff.load_handoff())


def cmd_log_suggestions(args):
    """Appends today's state/suggestions.json to the permanent history log."""
    with open(settings.suggestions_path()) as f:
        s = json.load(f)
    suggestion_log.append_entries(s["date"], s.get("holdings", {}), s.get("candidates", {}), s.get("etfs", {}))
    print(f"appended {s['date']} to {suggestion_log.log_path()}")


def cmd_dashboard(args):
    from position_watch.dashboard import publish, render

    if args.action == "build":
        if args.fragment:
            with open(args.fragment, "w") as f:
                f.write(render.build_fragment())
            print(f"wrote {args.fragment}")
        else:
            print(f"wrote {render.write_site(args.out)}")
    else:
        if not args.dir:
            sys.exit("usage: dashboard publish <path to the position-watch checkout>")
        try:
            path = publish.publish(args.dir)
        except publish.PasscodeMissing as exc:
            sys.exit(str(exc))
        where = settings.dashboard_url() or "the Pages site"
        print(f"wrote locked dashboard to {path}; live at {where} once pushed")


def cmd_daily(args):
    from position_watch import daily

    try:
        _print(daily.run(pages_dir=args.pages_dir, send_email=not args.no_email))
    except daily.StepFailed as exc:
        sys.exit(f"daily run failed at {exc.step}: {exc.error}")


def cmd_monthly(args):
    from position_watch import monthly

    _print(monthly.facts() if args.facts else monthly.run(send_email=not args.no_email))


def cmd_init(args):
    from position_watch import init

    written = init.create(args.dir)
    print(f"Created {len(written)} files in {args.dir}. Next: fill in config/people.json and "
          "data/processed/holdings.csv, then follow README.md (secrets, first run).")  # fmt: skip


def cmd_email_preview(args):
    """The daily email rebuilt from today's saved files (state/suggestions.json and latest_review.json)."""
    from pathlib import Path

    from position_watch.analysis import pnl
    from position_watch.dashboard import publish
    from position_watch.dashboard.view import load_inputs
    from position_watch.documents import render as documents

    holdings, latest, sugg = load_inputs()
    if not latest or not sugg:
        sys.exit("needs state/latest_review.json and state/suggestions.json from today's run")
    calls = {
        k: [{"symbol": s, **v} for s, v in (sugg.get(k) or {}).items()] for k in ("holdings", "etfs", "candidates")
    }
    money = pnl.compute(holdings, latest, latest.get("fx"))
    day = sugg["date"]
    msg = documents.email(day, latest, calls, money["totals"], settings.report_url(day), publish.email_link(),
                          args.dashboard_published, names={r["symbol"]: r.get("name") for r in holdings},
                          positions={p["symbol"]: p for p in money["positions"]})  # fmt: skip
    Path(args.html).write_text(msg["html"])
    Path(args.text).write_text(msg["text"])
    _print({"subject": msg["subject"], "html": args.html, "text": args.text})


def build_parser():
    parser = argparse.ArgumentParser(prog="position-watch", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a new portfolio repository from the template")
    p.add_argument("dir")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("daily", help="the whole morning run: evidence, one Claude call, report, dashboard, email")
    p.add_argument("--pages-dir", help="checkout of the position-watch Pages repo to publish the locked copy into")
    p.add_argument("--no-email", action="store_true", help="skip reading feedback and sending the email")
    p.set_defaults(func=cmd_daily)
    p = sub.add_parser("monthly", help="the monthly look-back and whole-portfolio review")
    p.add_argument("--no-email", action="store_true", help="write the report only")
    p.add_argument("--facts", action="store_true", help="print the computed facts only (no Claude call, no files)")
    p.set_defaults(func=cmd_monthly)
    p = sub.add_parser("email-preview", help="rebuild today's email (HTML and text files) from the saved state")
    p.add_argument("--html", default="email.html", help="where to write the HTML version")
    p.add_argument("--text", default="email.txt", help="where to write the plain-text version")
    p.add_argument("--dashboard-published", action="store_true", help="include the dashboard link")
    p.set_defaults(func=cmd_email_preview)
    sub.add_parser("check-env", help="say which secrets are set (never their values)").set_defaults(func=cmd_check_env)
    p = sub.add_parser("review", help="gather live evidence for holdings and the watchlist")
    p.add_argument("--no-watchlist", action="store_true", help="holdings only; skip the stock pool")
    p.set_defaults(func=cmd_review)
    sub.add_parser("pnl", help="P&L totals and the email's summary line").set_defaults(func=cmd_pnl)
    sub.add_parser("compliance", help="the closing note and action labels").set_defaults(func=cmd_compliance)
    sub.add_parser("people", help="print the people file").set_defaults(func=cmd_people)
    sub.add_parser("recipients", help="emails that get the daily review").set_defaults(func=cmd_recipients)
    p = sub.add_parser("who-sent", help="name and role for a sender, or null if unknown")
    p.add_argument("email")
    p.set_defaults(func=cmd_who_sent)
    p = sub.add_parser("prefs-update", help="record a lasting preference (changes as JSON on stdin)")
    p.add_argument("--source", required=True, help='e.g. "feedback <message id> from <name>"')
    p.set_defaults(func=cmd_prefs_update)
    p = sub.add_parser("handoff", help="show yesterday's notes, or save today's (JSON on stdin)")
    p.add_argument("action", choices=["show", "save"], nargs="?", default="show")
    p.set_defaults(func=cmd_handoff)
    sub.add_parser("log-suggestions", help="append state/suggestions.json to the history log").set_defaults(
        func=cmd_log_suggestions
    )
    p = sub.add_parser("dashboard", help="build the dashboard, or publish the locked copy")
    p.add_argument("action", choices=["build", "publish"])
    p.add_argument("dir", nargs="?", help="publish: path to the position-watch checkout")
    p.add_argument("--out", help="build: output path (default: workspace/site/index.html)")
    p.add_argument("--fragment", metavar="PATH", help="build: write the body-only Artifact version here")
    p.set_defaults(func=cmd_dashboard)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
